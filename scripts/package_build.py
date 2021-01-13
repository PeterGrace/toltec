#!/usr/bin/env python3
# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT
"""Build packages from a given recipe."""

import argparse
import logging
import os
import shutil
import sys
import docker
from toltec.recipe import Recipe
from toltec.util import argparse_add_verbose, LOGGING_FORMAT, query_user

parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)

parser.add_argument('recipe_dir',
                    metavar='RECIPEDIR',
                    help='directory where the recipe definition lives')

parser.add_argument('work_dir',
                    metavar='WORKDIR',
                    help='directory where the recipe will be built')

parser.add_argument(
    'packages_names',
    nargs='*',
    metavar='PACKAGENAME',
    help='list of packages to build (default: all packages from the recipe)')

argparse_add_verbose(parser)

args = parser.parse_args()
logging.basicConfig(format=LOGGING_FORMAT, level=args.verbose)

try:
    os.makedirs(args.work_dir)
except FileExistsError:
    ans = query_user(
        f"The working directory '{args.work_dir}' already exists.\nWould you \
like to [c]ancel, [r]emove the directory, or [k]eep it (not recommended)?",
        default='c',
        options=['c', 'r', 'k'],
        aliases={
            'cancel': 'c',
            'remove': 'r',
            'keep': 'k',
        })

    if ans == 'c':
        print('Build cancelled.')
        sys.exit()
    elif ans == 'r':
        shutil.rmtree(args.work_dir)
        os.makedirs(args.work_dir)

docker_client = docker.from_env()
recipe = Recipe.from_file(os.path.basename(args.recipe_dir), args.recipe_dir)

src_dir = os.path.join(args.work_dir, 'src')
os.makedirs(src_dir, exist_ok=True)

pkg_dir = os.path.join(args.work_dir, 'pkg')
os.makedirs(pkg_dir, exist_ok=True)

recipe.make(src_dir=src_dir,
            pkg_dir=pkg_dir,
            docker=docker_client,
            packages=args.packages_names if args.packages_names else None)
