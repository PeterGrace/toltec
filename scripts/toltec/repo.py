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
from typing import Dict, List, Optional
from docker.client import DockerClient
import requests
from .recipe import Recipe
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

    def fetch_packages(self, remote: Optional[str], fetch_missing: bool) \
            -> Dict[str, List[str]]:
        """
        Fetch missing packages.

        :param remote: remote server from which to check for existing packages
        :param fetch_missing: pass true to fetch missing packages from remote
        :returns: missing packages grouped by parent recipe
        """
        logger.info('Scanning for missing packages')
        missing: Dict[str, List[str]] = {}

        for recipe in self.recipes.values():
            missing[recipe.name] = []

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

                logger.info('Package %s (%s) is missing',
                    package.pkgid(), recipe.name)
                missing[recipe.name].append(package.name)

        return missing

    def make_packages(
            self, packages_by_recipe: Dict[str, List[str]],
            docker: DockerClient) -> None:
        """
        Build packages and move them to the repo.

        :param packages_by_recipe: packages to build (keys are recipes, values
            are list of packages for each recipe)
        :param docker: docker client to use for running the builds
        """
        logger.info('Building packages')

        for recipe_name, packages in packages_by_recipe.items():
            if packages:
                recipe = self.recipes[recipe_name]
                recipe_work_dir = os.path.join(self.work_dir, recipe_name)
                os.makedirs(recipe_work_dir, exist_ok=True)

                src_dir = os.path.join(recipe_work_dir, 'src')
                os.makedirs(src_dir, exist_ok=True)

                pkg_dir = os.path.join(recipe_work_dir, 'pkg')
                os.makedirs(pkg_dir, exist_ok=True)

                recipe.make(src_dir, pkg_dir, docker, packages)

                for package_name in packages:
                    package = recipe.packages[package_name]
                    filename = package.filename()
                    shutil.copy2(
                        os.path.join(pkg_dir, filename),
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
