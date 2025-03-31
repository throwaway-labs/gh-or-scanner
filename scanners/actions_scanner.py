import logging
import yaml
import base64
from collections import Counter
from datetime import datetime

from utils.github_client import GitHubClient
from scanners.base_scanner import BaseScanner

logger = logging.getLogger(__name__)

class GitHubActionsAnalyzer(BaseScanner):
    def __init__(self, token, org, storage_client=None):
        # Initialize GitHub client
        github_client = GitHubClient(token, org)
        super().__init__(github_client, storage_client)
        
    def extract_actions_from_workflow(self, content):
        """Extract GitHub Actions used in a workflow file"""
        actions = []
        
        try:
            # Try to parse as YAML
            workflow = yaml.safe_load(content)
            
            # Extract actions from jobs
            if 'jobs' in workflow:
                for job_name, job_config in workflow['jobs'].items():
                    # Look for steps
                    if 'steps' in job_config:
                        for step in job_config['steps']:
                            # Check if step uses an action
                            if 'uses' in step and step['uses']:
                                # Only include GitHub actions, not local paths or Docker images
                                action = step['uses']
                                if '/' in action and not action.startswith('./'):
                                    actions.append(action)
        except Exception as e:
            logger.error(f"Error parsing workflow: {e}")
        
        return actions

    def get_workflow_files(self, repo_name):
        """Get all workflow files for a repository"""
        url = f'{self.github_client.base_url}/repos/{self.org}/{repo_name}/contents/.github/workflows'
        response = self.github_client.make_request(url, expect_404=True)
        
        if response.status_code != 200:
            return []
            
        return response.json()

    def get_file_content(self, repo_name, file_path):
        """Get content of a file"""
        url = f'{self.github_client.base_url}/repos/{self.org}/{repo_name}/contents/{file_path}'
        response = self.github_client.make_request(url)
        
        if response.status_code != 200:
            return None
            
        content = response.json().get('content', '')
        if content:
            return base64.b64decode(content).decode('utf-8')
        return None

    def scan(self):
        """Analyze GitHub Actions usage across all repositories"""
        repos = self.github_client.get_all_repositories()
        
        all_actions = []
        repo_actions = {}
        repo_workflows = {}
        
        # Try to get organization-level data
        try:
            # Get organization-level Actions configuration
            logger.info(f"Fetching organization-level Actions configuration...")
            org_actions_config = self.github_client.get_org_actions_config()
            logger.info(f"Retrieved organization-level Actions configuration")
        except Exception as e:
            logger.warning(f"Error fetching organization Actions config: {e}")
            org_actions_config = {}
            
        try:
            # Get organization-level runners
            logger.info(f"Fetching organization-level runners...")
            org_runners = self.github_client.get_org_runners()
            logger.info(f"Retrieved {len(org_runners)} organization-level runners")
        except Exception as e:
            logger.warning(f"Error fetching organization runners: {e}")
            org_runners = []
        
        # Check rate limits before starting
        rate_limit = self.github_client.get_rate_limit()
        if rate_limit:
            logger.info(f"API calls remaining: {rate_limit.get('remaining', 0)}")
        
        for repo in repos:
            repo_name = repo['name']
            logger.info(f"Analyzing workflows for {repo_name}... ({repos.index(repo) + 1}/{len(repos)})")
            
            workflow_files = self.get_workflow_files(repo_name)
            
            if not workflow_files:
                logger.info(f"No workflows found for {repo_name}")
                continue
                
            actions_in_repo = []
            workflows_in_repo = []
            
            for workflow in workflow_files:
                # Skip directories or non-YAML files
                if workflow['type'] != 'file' or not (workflow['name'].endswith('.yml') or workflow['name'].endswith('.yaml')):
                    continue
                    
                content = self.get_file_content(repo_name, workflow['path'])
                if content:
                    workflow_data = {
                        'name': workflow['name'],
                        'path': workflow['path'],
                        'content_size': len(content) if content else 0
                    }
                    
                    actions = self.extract_actions_from_workflow(content)
                    if actions:
                        workflow_data['actions_count'] = len(actions)
                        workflow_data['actions'] = actions
                        actions_in_repo.extend(actions)
                        all_actions.extend(actions)
                        
                    workflows_in_repo.append(workflow_data)
            
            if actions_in_repo:
                repo_actions[repo_name] = actions_in_repo
                repo_workflows[repo_name] = workflows_in_repo
                logger.info(f"Found {len(actions_in_repo)} actions in {len(workflows_in_repo)} workflows in {repo_name}")
        
        # Count action usage
        action_counts = Counter(all_actions)
        
        # Group actions by publisher
        publishers = {}
        for action in action_counts:
            if '/' in action:
                publisher = action.split('/')[0]
                if publisher not in publishers:
                    publishers[publisher] = 0
                publishers[publisher] += action_counts[action]
        
        return {
            'org': self.org,
            'total_repositories': len(repos),
            'repositories_with_workflows': len(repo_actions),
            'action_counts': dict(action_counts),
            'publisher_counts': dict(Counter(publishers)),
            'total_actions_used': len(all_actions),
            'unique_actions_used': len(action_counts),
            'repository_actions': repo_actions,
            'repository_workflows': repo_workflows,
            'org_actions_config': org_actions_config,
            'org_runners': org_runners
        }
        
    def generate_report(self):
        """Generate a report of GitHub Actions usage"""
        logger.info("Analyzing GitHub Actions usage...")
        data = self.scan()
        
        # Generate basic report
        logger.info("=" * 50)
        logger.info(f"GitHub Actions Usage Report for {self.org}")
        logger.info("=" * 50)
        logger.info(f"Total repositories: {data['total_repositories']}")
        logger.info(f"Repositories with workflows: {data['repositories_with_workflows']}")
        logger.info(f"Total actions used: {data['total_actions_used']}")
        logger.info(f"Unique actions used: {data['unique_actions_used']}")
        
        # Organization configuration
        org_actions_enabled = data.get('org_actions_config', {}).get('enabled_repositories') != 'none'
        logger.info(f"Organization Actions enabled: {org_actions_enabled}")
        
        # Organization runners
        org_runners = data.get('org_runners', [])
        logger.info(f"Organization runners: {len(org_runners)}")
        
        logger.info("\nTop 20 most used actions:")
        for action, count in Counter(data['action_counts']).most_common(20):
            logger.info(f"  {action}: {count}")
            
        logger.info("\nTop publishers:")
        for publisher, count in Counter(data['publisher_counts']).most_common(10):
            logger.info(f"  {publisher}: {count}")
        
        # Save report
        data = self.save_report(data)
        logger.info(f"Detailed report saved to {data['report_file']['local_path']}")
        if data['report_file']['gcs_path']:
            logger.info(f"Report uploaded to {data['report_file']['gcs_path']}")
            
        return data