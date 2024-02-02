#!/usr/bin/env python3
import sys
import os
from typing import Union
import requests
import argparse
import logging
from git import Repo
from atlassian import Bitbucket
from tqdm import tqdm
import dotenv


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    dotenv.load_dotenv()

    GH_TOKEN = os.getenv('GITHUB_TOKEN')
    BB_TOKEN = os.getenv('BB_TOKEN')
    BB_USERNAME = os.getenv('BB_USER')

    credentials_env = {"GIT_USERNAME": BB_USERNAME, "GIT_PASSWORD": BB_TOKEN}

    if GH_TOKEN is None or BB_TOKEN is None:
        logger.error('Please provide GitHub and Bitbucket tokens in .env file')
        exit(1)

    argparser = argparse.ArgumentParser()
    argparser.add_argument('--bb-repo-url', required=False, default='https://code.devrtb.com/scm/rtb/server.git', type=str, help='Bitbucket repository URL')
    argparser.add_argument('--bb-api-url', required=False, default='https://code.devrtb.com', type=str, help='Bitbucket API URL')
    argparser.add_argument('--gh-repo-url', required=False, default='https://github.com/miroapp-dev/server', type=str, help='GitHub repository URL')
    argparser.add_argument('--pr-ids', required=False, nargs='+', type=int, help='List of PR IDs to tag')
    argparser.add_argument('--dry-run', required=False, default=False, action='store_true', help='Dry run mode')
    argparser.add_argument('--log-level', required=False, default='INFO', type=str, help='Log level')
    argparser.add_argument('--repo-dir', required=False, default=os.path.join(os.getcwd(), 'git-repo'), type=str, help='Directory to store git repositoriy')
    argparser.add_argument('--bb-project', required=False, default='RTB', type=str, help='Bitbucket project name')
    argparser.add_argument('--bb-repo-slug', required=False, default='server', type=str, help='Bitbucket repository slug')

    argparser.description = r'''Transfer PRs from Bitbucket to GitHub.
    This script tags the latest commit in the PR and pushes the tag to the GitHub PR branch.
    '''

    args = argparser.parse_args()
    # check is log lever is valid
    if args.log_level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
        logger.error('Invalid log level provided. Please provide one of DEBUG, INFO, WARNING, ERROR, CRITICAL')
        exit(1)

    logging.getLogger().setLevel(args.log_level)

    if args.pr_ids is None and sys.stdin.isatty():
        logger.error('Please provide either --pr-ids or input data via stdin')
        exit(1)

    if not sys.stdin.isatty():
        logger.info('Reading PR IDs from stdin')
        # open stdin and read newline separated PR IDs
        pr_ids = [int(pr_id) for pr_id in sys.stdin.read().split('\n') if pr_id]
        #check if pr_ids only integers
        if not all(isinstance(pr_id, int) for pr_id in pr_ids):
            logger.error('Please provide only integers')
            exit(1)

    logger.info(f'Bitbucket repository URL: {args.bb_repo_url}')

    repo = None
    # if repo dir not exist clone it
    if not os.path.exists(args.repo_dir):
        logger.info(f'Cloning {args.bb_repo_url} to {args.repo_dir} (shallow clone)')
        # use tqdm to show progress bar
        with tqdm(total=100) as pbar:
            # defining updater function that implements CallableProgress type
            def updater(op_code: int, cur_count: Union[str, float], max_count: Union[str, float, None], message: str) -> None:
                pbar.update(int(cur_count))
                pbar.total = int(max_count) if max_count else pbar.total

            repo = Repo.clone_from(args.bb_repo_url, args.repo_dir, multi_options=['--bare', '--depth=1'], progress=updater, env=credentials_env)
    else:
        # if repo dir exist open it
        repo = Repo.init(args.repo_dir, bare=True)
        with tqdm(total=100) as pbar:
            logger.info(f'Fetching tags for {args.bb_repo_url}')
            def updater(op_code: int, cur_count: Union[str, float], max_count: Union[str, float, None], message: str) -> None:
                pbar.update(int(cur_count))
                pbar.total = int(max_count) if max_count else pbar.total
            repo.remotes.origin.fetch('+refs/tags/*:refs/tags/*', progress=updater, env=credentials_env)

    # check if origin github exists and set to args.gh_repo_url
    if 'github' not in [r.name for r in repo.remotes]:
        logger.info(f'Adding GitHub repository as remote')
        repo.create_remote('github', args.gh_repo_url)

    if repo.remotes.github.url != args.gh_repo_url:
        logger.error(f'GitHub remote URL mismatch: {repo.remotes.github.url} != {args.gh_repo_url}')
        exit(1)

    logger.info('Checking Bitbucket API')
    bb = Bitbucket(url=args.bb_api_url, username=BB_USERNAME, password=BB_TOKEN)
    try:
        projects = bb.project_list(limit=1)
        # iterate over projects gereator without using it
        for p in projects:
            logger.debug(p)
    except requests.exceptions.HTTPError as e:
        logger.exception(f'Bitbucket API error: {e.response}')
        exit(1)
    except Exception as e:
        logger.exception(f'Bitbucket API error: {e}')
        exit(1)

    logger.info('Scanning PRs for project: %s, repo: %s, total PRs: %s', args.bb_project, args.bb_repo_slug, len(args.pr_ids))
    for pr in args.pr_ids:
        logger.info(f'Processing PR: {pr}')
        try:
            pr = bb.get_pull_request(args.bb_project, args.bb_repo_slug, pr)
            logger.debug(f'PR: {pr}')
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(f'PR {pr} not found')
                continue
        except Exception as e:
            logger.exception(f'Bitbucket API error: {e}')
            continue

        if pr['fromRef']['latestCommit'] is None:
            logger.error(f'PR {pr} has no commits')
            continue

        commit_hash = pr['fromRef']['latestCommit']
        tag_name = f"dig-pr_{pr['id']}"
        logger.info(f'Tagging commit {commit_hash} with tag {tag_name}')

        if not args.dry_run:
            try:
                bb.set_tag(args.bb_project, args.bb_repo_slug, tag_name, commit_hash)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 409:
                    logger.info(f'Tag {tag_name} already exists')
            except Exception as e:
                logger.exception(f'Bitbucket API error: {e}')
                continue

    logger.info('Done tagging PRs')

    with tqdm(total=100) as pbar:
        logger.info(f'Fetching tags for {args.bb_repo_url}')
        def updater(op_code: int, cur_count: Union[str, float], max_count: Union[str, float, None], message: str) -> None:
            pbar.update(int(cur_count))
            pbar.total = int(max_count) if max_count else pbar.total
        repo.remotes.origin.fetch('+refs/tags/*:refs/tags/*', progress=updater, env=credentials_env)

    # push tags to github
    for pr in args.pr_ids:
        tag_name = f"dig-pr_{pr}"
        logger.info(f'Pushing tag {tag_name} to GitHub')
        if not args.dry_run:
            try:
                repo.remotes.github.push(f'refs/tags/{tag_name}:refs/tags/{tag_name}')
            except Exception as e:
                logger.exception(f'Error pushing tag {tag_name} to GitHub: {e}')
                continue

if __name__ == '__main__':
    main()