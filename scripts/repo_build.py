#!/usr/bin/env python3
# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""Build all packages and create a package index."""

import argparse
import logging
import sys
from toltec.repo import Repo
from toltec.util import LOGGING_FORMAT

parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)

parser.add_argument(
    'recipes_dir', metavar='RECIPESDIR',
    help='directory from which package recipes are read')

parser.add_argument(
    'work_dir', metavar='WORKDIR',
    help='directory in which packages are built')

parser.add_argument(
    'repo_dir', metavar='REPODIR',
    help='staging directory in which the repo is constructed')

parser.add_argument(
    '-n', '--no-fetch', action='store_true',
    help='do not fetch missing packages from the remote repository')

parser.add_argument(
    '-v', '--verbose', action='store_const',
    const=logging.DEBUG, default=logging.INFO,
    help='show debugging information')

group = parser.add_mutually_exclusive_group()

group.add_argument(
    '-l', '--local', action='store_true',
    help='''by default, packages missing from the local repository are not
    rebuilt if they already exist on the remote repository — pass this flag to
    disable this behavior''')

group.add_argument(
    '-r', '--remote-repo',
    default='https://toltec-dev.org/testing', metavar='URL',
    help='''root of a remote repository used to know which packages
    are already built (default: %(default)s)''')

args = parser.parse_args()
remote = args.remote_repo if not args.local else None
logging.basicConfig(format=LOGGING_FORMAT, level=args.verbose)

repo = Repo(args.recipes_dir, args.work_dir, args.repo_dir)
repo.make_packages(remote, fetch_missing=not args.no_fetch)
repo.make_index()
