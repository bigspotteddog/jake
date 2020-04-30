"""jake entry point"""
# pylint: disable=too-many-arguments
# pylint: disable=invalid-name
# pylint: disable=unnecessary-pass
# Copyright 2019 Sonatype Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import logging
from os import _exit, EX_OSERR, path, mkdir
from pathlib import Path

import click
from termcolor import cprint
from pyfiglet import figlet_format
from colorama import init, Fore
from yaspin import yaspin

from .ossindex.ossindex import OssIndex
from .iq.iq import IQ
from .cyclonedx.generator import CycloneDxSbomGenerator
from .parse.parse import Parse
from .pip.pip import Pip
from .audit.audit import Audit
from .config.config import Config
from .config.iq_config import IQConfig
from ._version import __version__

# strip colors on redirected output
init(strip=not sys.stdout.isatty())

def __print_version(ctx, flag: bool):
  if not flag:
    return
  print(__package__, 'v' +  __version__)
  ctx.exit()

def __clear_cache(ctx, flag: bool):
  if not flag:
    return
  ossi = OssIndex()
  if ossi.clean_cache():
    print('Cache Cleared')
  ctx.exit()

def __check_stdin(flag: bool):
  if flag and sys.stdin.isatty():
    print('No stdin detected, run \'conda list | jake ...\' with the \'-c\' flag.')
    _exit(0)
  else:
    return

# params that propagate through subcommands
__shared_options = [
    click.option(
        '-vv', '--verbose',
        is_flag=True,
        default=False,
        help='Set log level to verbose'),
    click.option(
        '-q', '--quiet',
        is_flag=True,
        default=False,
        help='Suppress cosmetic and informational output'),
    click.option(
        '-c', '--conda',
        default=False,
        is_flag=True,
        help='Resolve conda dependencies from std_in'),
    click.option(
        '-t', '--targets',
        default=None,
        help='List of site packages containing modules to be evaluated')
]

# decorators be parsed inside out which click handles, but no decorators on the shared options
def __add_options(options):
  def _add_options(func):
    for option in reversed(options):
      func = option(func)
    return func
  return _add_options

@click.group(help='Jake: Put your python deps in a chokehold.')
# options that will take priority over other program execution and exit
@click.option(
    '-v', '--version',
    is_flag=True,
    callback=__print_version,
    expose_value=False,
    is_eager=True,
    help='Print version and exit')
@click.option(
    '--clear',
    is_flag=True,
    callback=__clear_cache,
    expose_value=False,
    is_eager=True,
    help='Clear the OSS Index cache and exit')

# entry point, the above options get executed first as callbacks
def main():
  """ defining the root cli command as main so that running 'jake'
      in the command line will use this as the entry point
      also prints the banner with every invokation

  Arguments:
      version -- jake flag that will print version and exit
      verbose -- get full runtime output from debug logger
      quiet -- supress the banner TODO: non vulnerable outputs as well
  """

@main.command()
@click.argument(
    'conf',
    type=click.Choice(['iq', 'ossi']))
def config(conf):
  """
  Allows a user to set Nexus IQ or OSS Index config params

  Arguments:
      type -- cli input restricted by click to 'iq' and 'ossi'
  """
  cli_config = IQConfig() if conf == 'iq' else Config()

  # exits 0 if config was set, with non-zero from os if it failed
  result = cli_config.get_config_from_std_in()
  if result is False:
    _exit(EX_OSERR)
  else:
    _exit(0)

@main.command()
@__add_options(__shared_options)
@click.option(
    '-o', '--output',
    default='bom.xml',
    help='Specify a file name and/or directory to save the CycloneDx sbom')
def sbom(verbose, quiet, conda, targets, output):
  """
  Generates a purl only bom (no vulns) and outputs it to a file
  that can be picked up by a Sonatype CLI or CI Plugin

  Does not make any connection to IQ or OSSI

  Arguments:
    output -- file name, relative or absolute path (w/ file name)
  """
  __banner(quiet)
  __setup_logger(verbose)
  __check_stdin(conda)

  sbom_xml = __sbom_control_flow(conda, targets).decode('utf-8')
  with open(output, 'w') as bom_file:
    print(sbom_xml, file=bom_file)
  _exit(0)

# ddt (ossi) subcommand
@main.command()
@__add_options(__shared_options)
def ddt(verbose, quiet, conda, targets):
  """SPECIAL MOVE\n
  Allows you to perform scans backed by Sonatype's OSS Index

  Example usage:\n
      Python scan: jake ddt\n
      Conda scan: conda list | jake ddt -c\n
  """
  __banner(quiet)
  __setup_logger(verbose)
  __check_stdin(conda)

  with yaspin(text="Loading", color="yellow") as spinner:
    spinner.text = "Collecting Dependencies"
    coords = Parse().get_deps_stdin(sys.stdin) if conda else Pip(targets).get_dependencies()
    spinner.ok("🐍 ")

  with yaspin(text="Loading", color="yellow") as spinner:
    spinner.text = "Querying OSS Index"

    response = OssIndex().call_ossindex(coords)

    if response is None:
      spinner.fail("💥 ")
      click.echo(
          "Something went horribly wrong, there is no response from OSS Index",
          "please rerun with -VV to see what happened")
      _exit(EX_OSERR)
    spinner.ok("🐍 ")

  with yaspin(text="Loading", color="yellow") as spinner:
    spinner.text = "Auditing results from OSS Index"
    audit = Audit(quiet)
    spinner.ok("🐍 ")
    code = audit.audit_results(response)
    _exit(code)

@main.command()
@__add_options(__shared_options)
@click.option(
    '-a', '--application',
    help='Supply an IQ Server Public Application ID',
    required=True)
@click.option(
    '-s', '--stage',
    default='develop',
    type=click.Choice(['develop', 'build', 'stage-release', 'release']),
    help='Specify a stage')
@click.option(
    '-u', '--user',
    help='Set username for Sonatype IQ')
@click.option(
    '-p', '--password',
    help='Set password or token for associated user')
@click.option(
    '-h', '--host',
    help='Specify an endpoint for Sonatype IQ')
def iq(verbose: bool, quiet: bool, conda: bool, targets: str,
       application, stage, user, password, host):
  """EXTRA SPECIAL MOVE\n
  Allows you to perform scans backed by Sonatype's Nexus IQ Server

  Example usage:\n
      Python scan: jake iq -a <AppId>\n
      Conda scan: conda list | jake iq -a <AppId> -c\n

  Will pull values for other params from config unless overwritten here\n

      To set the IQ config: jake config iq\n
  """
  __banner(quiet)
  __setup_logger(verbose)
  __check_stdin(conda)
  bom = __sbom_control_flow(conda, targets)

  iq_args = {}
  iq_args['application'] = application
  iq_args['stage'] = stage
  iq_args['user'] = user
  iq_args['password'] = password
  iq_args['host'] = host
  iq_args['conda'] = conda

  __iq_control_flow(iq_args, bom)

def __setup_logger(verbose: bool):
  logger = logging.getLogger('jake')
  logger.setLevel(logging.DEBUG)

  home = str(Path.home())
  if not path.exists(path.join(home, '.ossindex')):
    mkdir(path.join(home, '.ossindex'))

  filepath = path.join(home, '.ossindex', 'jake.combined.log')
  formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

  fh = logging.FileHandler(filepath)

  logger.addHandler(fh)

  ch = logging.StreamHandler()

  if verbose:
    ch.setLevel(logging.DEBUG)
  else:
    ch.setLevel(logging.ERROR)

  fh.setFormatter(formatter)
  ch.setFormatter(formatter)

  logger.addHandler(ch)

def __iq_control_flow(args: dict, bom_str: bytes):
  with yaspin(text="Loading", color="magenta") as spinner:
    spinner.text = "Submitting to Sonatype IQ..."
    iq_requests = IQ(args)
    _id = iq_requests.get_internal_id()
    status_url = iq_requests.submit_sbom(bom_str, _id)
    spinner.ok("🐍 ")

  with yaspin(text="Loading", color="magenta") as spinner:
    spinner.text = "Reticulating splines..."
    iq_requests.poll_report(status_url)

    if iq_requests.get_policy_action() is not None:
      spinner.fail("💥 ")
      print(Fore.YELLOW +
            "Snakes on the plane! There are policy failures from Sonatype IQ.")
      print(Fore.YELLOW +
            "Your IQ Server Report is available here: {}".format(iq_requests.get_report_url()))
      _exit(1)
    else:
      spinner.ok("🐍 ")
      print(Fore.GREEN +
            "Smooth slithering there bud! No policy failures from Sonatype IQ.")
      print(Fore.GREEN +
            "Your IQ Server Report is available here: {}".format(iq_requests.get_report_url()))
      _exit(0)

def __sbom_control_flow(conda: bool, target: str) -> (bytes):
  """
  Gets the purls depending on the format and generates the sbom

  Arguments:
      conda -- whether to get conda deps from stdin

  Returns:
      bytestring of the sbom
  """
  with yaspin(text="Loading", color="yellow") as spinner:
    spinner.text = "Collecting Dependencies from System..."
    coords = Parse().get_deps_stdin(sys.stdin) if conda else Pip(target).get_dependencies()
    spinner.ok("🐍 ")
    spinner.text = "Parsing Coordinates..."
    purls = coords.get_purls()
    spinner.ok("🐍 ")

  with yaspin(text="Loading", color="magenta") as spinner:
    spinner.text = "Generating CycloneDx BOM..."
    sbom_gen = CycloneDxSbomGenerator()
    sbom_xml = sbom_gen.purl_sbom(purls)
    sbom_byte_str = sbom_gen.sbom_to_string(sbom_xml)
    spinner.ok("🐍 ")

  return sbom_byte_str

def __banner(quiet: bool):
  """ Prints the banner, most of the user facing commands start with this """
  if quiet:
    return
  top_font = 'isometric4' # another option: 'isometric1'
  bot_font = 'invita'
  top = 'Jake'
  bot = ' ..the snake..'
  cprint(figlet_format(top, font=top_font), 'green', attrs=[])
  cprint(figlet_format(bot, font=bot_font), 'blue', attrs=['dark'])
  click.echo("Jake version: v{}".format(__version__))
  click.echo('Put your python deps in a chokehold.')

if __name__ == '__main__':
  main()
