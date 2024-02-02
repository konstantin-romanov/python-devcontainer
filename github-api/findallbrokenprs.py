import os
from github import Github
from github.GithubException import UnknownObjectException
import requests
import dotenv
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
import argparse
import os
import logging

# consts
BROKEN_PRS_FILE = 'broken_prs.txt'
PR_NOT_FOUND_FILE = 'pr_not_found.txt'
PR_EXCEPTIONS_FILE = 'pr_exceptions.txt'
LAST_ISSUE_FILE = 'last_issue.txt'

class SimpleIssue:
    def __init__(self, number):
        self._number = number

    @property
    def number(self):
        return self._number


def write_to_file(file, data, mode='a', dry_run=False, newline=True):
    if dry_run:
        logging.info(f'[Dry mode] Would write to {file}: {data}')
    else:
        with open(file, mode) as f:
            if newline:
                f.write(f'{data}\n')
            else:
                f.write(data)

def main():
    logging.basicConfig(level=logging.INFO)

    logger = logging.getLogger(__name__)

    # Load environment variables
    logger.info('Loading environment variables')
    dotenv.load_dotenv()

    token = os.getenv('GITHUB_TOKEN')

    if token is None:
        logger.error('Please set GITHUB_TOKEN environment variable')
        exit(1)

    argparser = argparse.ArgumentParser()
    argparser.add_argument('--start', required=False, default=None, type=int, help='Start value for the range of PRs to check')
    argparser.add_argument('--end', required=False, default=None, type=int, help='End value for the range of PRs to check')
    argparser.add_argument('-p', '--pr', required=False, nargs='+', default=None, type=int, help='PR number to check')
    argparser.add_argument('--state-dir', required=False, default=os.path.join(os.getcwd(), 'data'), type=str, help='Directory to store state files')
    argparser.add_argument('--dry-run', required=False, default=False, action='store_true', help='Dry run mode')
    argparser.add_argument('--log-level', required=False, default='INFO', type=str, help='Log level')
    argparser.add_argument('--repo', required=False, default='miroapp-dev/server', type=str, help='Repository to check PRs for')
    argparser.description = r'''Find all broken PRs.
    Examples:
    python findallbrokenprs.py --start 1 --end 1000 --dry-run # will check PRs from 1 to 1000 and not create state files
    python findallbrokenprs.py --pr 18057 32559 32599 --dry-run # will check PRs <broken diff>, <normal diff>, <not found> and not create state files
    '''

    args = argparser.parse_args()

    g = Github(token)

    if args.pr is None and args.start is None and args.end is None:
        logger.error('Please provide either --pr, --start and --end arguments')
        exit(1)

    if args.pr is not None and (args.start is not None or args.end is not None):
        logger.error('Please provide either --pr or --start and --end arguments')
        exit(1)

    logger.info('generating issues list')

    last_issue_file = os.path.join(args.state_dir, LAST_ISSUE_FILE)
    broken_prs_file = os.path.join(args.state_dir, BROKEN_PRS_FILE)
    pr_not_found_file = os.path.join(args.state_dir, PR_NOT_FOUND_FILE)
    pr_ex_file = os.path.join(args.state_dir, PR_EXCEPTIONS_FILE)

    if args.pr is not None:
        issues = [SimpleIssue(pr) for pr in args.pr]
    else:
        issues = [SimpleIssue(num) for num in range(args.start, args.end)]

    if args.dry_run is False:
        os.makedirs(args.state_dir, exist_ok=True)
    else:
        logger.info('Dry run mode, not creating state files')
    # end_value = 30165 # got this number from github search using abot query

    # Loop through each issue
    logger.info('Scanning issues for broken PRs, total issues: %s', len(issues))
    with logging_redirect_tqdm(loggers=[logger, logging.root]):
        for issue in tqdm(
                issues,
                unit='issue',
                total=len(issues),
            ):
            try:
                pr_number = issue.number
                logger.info('Checking PR number: %s', pr_number)

                # Request the diff for the PR
                try:
                    pr = g.get_repo("miroapp-dev/server").get_pull(pr_number)
                except UnknownObjectException as e:
                    logger.info('PR number: %s not found', pr_number)

                    write_to_file(pr_not_found_file, pr_number, dry_run=args.dry_run)
                    continue

                if pr.commits == 0:
                    logger.info('PR number: %s looks suspiciously empty', pr_number)

                    headers = {
                        'Authorization': f'Bearer {token}',
                        'Accept': 'application/vnd.github.diff',
                    }
                    response = requests.get(pr.diff_url, headers=headers)
                    if response.status_code in [404, 422]:
                        logger.info('PR number: %s has no diff. thats our guy', pr_number)

                        # append pr number to file
                        write_to_file(broken_prs_file, pr_number, dry_run=args.dry_run)

                write_to_file(last_issue_file, pr_number, dry_run=args.dry_run, newline=False)

            except Exception as e:
                logger.exception('Exception occurred for PR number: %s', pr_number)

                write_to_file(pr_ex_file, pr_number, dry_run=args.dry_run)

    logger.info('Done!')


if __name__ == '__main__':
    main()

# Define the search query
# query = "repo:miroapp-dev/server is:pr created:<2023-12-01 -label:\"Broken PR\""

# Search for issues using the query
# issues = g.search_issues(query)
# print(f"Found {issues.totalCount} issue(s)")

# # broken issue with no diff, normal issue with diff
# issues = [Issue(18057), Issue(32559)]

# Generate issues iterable object
# if last_issue file exists open it and use it as start value
