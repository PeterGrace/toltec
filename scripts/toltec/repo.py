# Copyright (c) 2021 The Toltec Contributors
# SPDX-License-Identifier: MIT

"""
Build the package repository.
"""

from datetime import datetime
import gzip
import logging
import os
import shutil
import subprocess
from typing import Dict, List, Optional
import requests
from .recipe import Recipe, Package
from .util import file_sha256, HTTP_DATE_FORMAT

logger = logging.getLogger(__name__)


class Repo:
    """Repository of Toltec packages."""
    def __init__(self, recipes_dir: str, work_dir: str, repo_dir: str):
        """
        Initialize the package repository.

        :param recipes_dir: directory from which package recipes are read
        :param work_dir: directory in which packages are built
        :param repo_dir: staging directory in which the repo is constructed
        """
        self.recipes_dir = recipes_dir
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)

        self.repo_dir = repo_dir
        os.makedirs(repo_dir, exist_ok=True)

        self.recipes = {}

        for name in os.listdir(recipes_dir):
            if name[0] != '.':
                self.recipes[name] = Recipe.from_file(name,
                    os.path.join(recipes_dir, name))

    def make_packages(self, remote: Optional[str], fetch_missing: bool) -> None:
        """Fetch missing packages and build new packages."""
        missing: Dict[str, List[Package]] = {}
        logger.info('Building a local repository')

        for recipe in self.recipes.values():
            missing[recipe.name] = []
            logger.info('Processing recipe %s', recipe.name)

            for package in recipe.packages.values():
                filename = package.filename()
                local_path = os.path.join(self.repo_dir, filename)

                if os.path.isfile(local_path):
                    continue

                if remote is not None:
                    remote_path = os.path.join(remote, filename)

                    if fetch_missing:
                        req = requests.get(remote_path)

                        if req.status_code == 200:
                            logger.info('Found %s on remote repo', package.name)

                            with open(local_path, 'wb') as local:
                                for chunk in req.iter_content(chunk_size=1024):
                                    local.write(chunk)

                            last_modified = int(datetime.strptime(
                                req.headers['Last-Modified'],
                                HTTP_DATE_FORMAT).timestamp())

                            os.utime(local_path, (last_modified, last_modified))
                            continue
                    else:
                        req = requests.head(remote_path)
                        if req.status_code == 200:
                            continue

                missing[recipe.name].append(package)

        # Build missing packages
        for recipe_name, packages in missing.items():
            if packages:
                logger.info('Building missing package(s): %s', packages)
                subprocess.run([
                    'scripts/package-build',
                    os.path.join(self.recipes_dir, recipe_name),
                    os.path.join(self.work_dir, recipe_name),
                    *[package.name for package in packages]
                ], check=True)

                for package in packages:
                    filename = package.filename()
                    shutil.copy2(
                        os.path.join(
                            self.work_dir, recipe_name,
                            package.name, filename),
                        self.repo_dir)

    def make_index(self) -> None:
        """Generate index files for all the packages in the repo."""
        logger.info('Generating package index')
        index_path = os.path.join(self.repo_dir, 'Packages')
        index_gzip_path = os.path.join(self.repo_dir, 'Packages.gz')

        with open(index_path, 'w') as index_file:
            with gzip.open(index_gzip_path, 'wt') as index_gzip_file:
                for recipe in self.recipes.values():
                    for package in recipe.packages.values():
                        filename = package.filename()
                        local_path = os.path.join(self.repo_dir, filename)

                        if not os.path.isfile(local_path):
                            continue

                        control = package.control_fields()
                        control += f'''Filename: {filename}
SHA256sum: {file_sha256(local_path)}
Size: {os.path.getsize(local_path)}

'''

                        index_file.write(control)
                        index_gzip_file.write(control)
