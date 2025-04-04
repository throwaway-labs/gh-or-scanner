import logging
from collections import Counter
from datetime import datetime

from utils.github_client import GitHubClient
from scanners.base_scanner import BaseScanner

logger = logging.getLogger(__name__)

class GitHubSecurityAnalyzer(BaseScanner):
    def __init__(self, token=None, org=None, storage_client=None, repo_limit=0, client=None):
        """
        Initialize GitHubSecurityAnalyzer with either a GitHub client or token/org pair.
        
        Args:
            token: GitHub token (legacy mode)
            org: GitHub organization name (legacy mode)
            storage_client: Optional cloud storage client
            repo_limit: Maximum number of repositories to scan (0 = no limit)
            client: Pre-configured GitHubClient instance (preferred)
        """
        # Use provided GitHub client or create one
        if client:
            github_client = client
            self.org = client.org
        else:
            # Legacy mode - create client with token
            github_client = GitHubClient(token, org)
            self.org = org
            
        super().__init__(github_client, storage_client)
        self.repo_limit = repo_limit
    
    def get_org_security_advisories(self):
        """Get security advisories at the organization level"""
        url = f'{self.github_client.base_url}/orgs/{self.org}/security-advisories'
        response = self.github_client.make_request(url, expect_404=True)
        
        if response.status_code != 200:
            logger.warning(f"Unable to fetch organization-level security advisories for {self.org}")
            return []
            
        return response.json()
        
    def get_org_dependabot_alerts(self):
        """Get all Dependabot alerts for the organization"""
        url = f'{self.github_client.base_url}/orgs/{self.org}/dependabot/alerts?state=open&per_page=100'
        return self.github_client.get_paginated_results(url)
        
    def get_org_secret_scanning_alerts(self):
        """Get all secret scanning alerts for the organization"""
        url = f'{self.github_client.base_url}/orgs/{self.org}/secret-scanning/alerts?state=open&per_page=100'
        return self.github_client.get_paginated_results(url)
    
    def get_org_code_scanning_alerts(self):
        """Get all code scanning alerts for the organization"""
        url = f'{self.github_client.base_url}/orgs/{self.org}/code-scanning/alerts?state=open&per_page=100'
        return self.github_client.get_paginated_results(url)
    
    def check_security_features(self, repo_name):
        """Check security features enabled for a repository"""
        url = f'{self.github_client.base_url}/repos/{self.org}/{repo_name}/security-and-analysis'
        response = self.github_client.make_request(url, expect_404=True)
        
        if response.status_code != 200:
            return {
                "advanced_security": {"status": "disabled"},
                "secret_scanning": {"status": "disabled"},
                "secret_scanning_push_protection": {"status": "disabled"}
            }
            
        return response.json()

    def check_vulnerability_alerts(self, repo_name):
        """Check if vulnerability alerts are enabled"""
        url = f'{self.github_client.base_url}/repos/{self.org}/{repo_name}/vulnerability-alerts'
        response = self.github_client.make_request(url, expect_404=True)
        
        # 204 means enabled, 404 means disabled
        return response.status_code == 204

    def check_automated_security_fixes(self, repo_name):
        """Check if automated security fixes are enabled"""
        url = f'{self.github_client.base_url}/repos/{self.org}/{repo_name}/automated-security-fixes'
        response = self.github_client.make_request(url, expect_404=True)
        
        if response.status_code != 200:
            # Check for dependabot.yml file as an alternative way to detect automated fixes
            contents = self.github_client.get_repository_contents(repo_name, '.github')
            if isinstance(contents, list):
                for item in contents:
                    if item.get('name') == 'dependabot.yml' or item.get('name') == 'dependabot.yaml':
                        return True
            return False
            
        return response.json().get('enabled', False)

    def scan(self, repo_limit=0):
        """Analyze security status across all repositories"""
        repos = self.github_client.get_all_repositories()
        
        # Apply repository limit if specified
        limit = repo_limit or self.repo_limit
        if limit > 0 and len(repos) > limit:
            logger.info(f"Limiting scan to {limit} repositories (out of {len(repos)} total)")
            repos = repos[:limit]
        
        # Check rate limits before starting
        rate_limit = self.github_client.get_rate_limit()
        if rate_limit:
            logger.info(f"API calls remaining: {rate_limit.get('remaining', 0)}")
        
        # Track archived repositories
        archived_repos_count = 0
        archived_with_advanced_security = 0
        archived_with_secret_scanning = 0
        archived_with_push_protection = 0
        archived_with_vuln_alerts = 0
        archived_with_auto_fixes = 0
        
        # Track archived repos with alerts
        archived_with_secret_alerts = 0
        archived_with_code_alerts = 0
        archived_with_dependabot_alerts = 0
        
        # Initialize data structures
        security_data = {
            'org': self.org,
            'total_repositories': len(repos),
            'archived_repositories': 0,  # Will update this
            'active_repositories': 0,    # Will update this
            'security_features': {
                'advanced_security_enabled': 0,
                'secret_scanning_enabled': 0,
                'secret_scanning_push_protection_enabled': 0,
                'vulnerability_alerts_enabled': 0,
                'automated_security_fixes_enabled': 0
            },
            'alert_counts': {
                'repositories_with_secret_alerts': 0,
                'repositories_with_code_alerts': 0, 
                'repositories_with_dependabot_alerts': 0,
                'total_secret_scanning_alerts': 0,
                'total_code_scanning_alerts': 0,
                'total_dependabot_alerts': 0
            },
            'archive_stats': {
                'archived_with_advanced_security': 0,
                'archived_with_secret_scanning': 0,
                'archived_with_push_protection': 0,
                'archived_with_vulnerability_alerts': 0,
                'archived_with_auto_fixes': 0,
                'archived_with_secret_alerts': 0,
                'archived_with_code_alerts': 0,
                'archived_with_dependabot_alerts': 0
            },
            'repositories': [],
            'repository_metadata': {},  # Will store detailed repo metadata
            'repo_limit_applied': limit if limit > 0 else None
        }
        
        # Get organization-level alerts first (more efficient)
        logger.info(f"Fetching organization-level alerts for {self.org}...")
        
        # Get all dependabot alerts at organization level
        try:
            dependabot_alerts = self.github_client.get_org_dependabot_alerts()
            logger.info(f"Retrieved {len(dependabot_alerts)} organization-level Dependabot alerts")
        except Exception as e:
            logger.warning(f"Error fetching organization-level Dependabot alerts: {e}")
            dependabot_alerts = []
        
        # Get all secret scanning alerts at organization level
        try:
            secret_alerts = self.github_client.get_org_secret_scanning_alerts()
            logger.info(f"Retrieved {len(secret_alerts)} organization-level Secret Scanning alerts")
        except Exception as e:
            logger.warning(f"Error fetching organization-level Secret Scanning alerts: {e}")
            secret_alerts = []
        
        # Get all code scanning alerts at organization level
        try:
            code_alerts = self.github_client.get_org_code_scanning_alerts()
            logger.info(f"Retrieved {len(code_alerts)} organization-level Code Scanning alerts")
        except Exception as e:
            logger.warning(f"Error fetching organization-level Code Scanning alerts: {e}")
            code_alerts = []
        
        # Create dictionaries for quick lookups by repository
        repo_to_dependabot_alerts = {}
        repo_to_secret_alerts = {}
        repo_to_code_alerts = {}
        
        # Process dependabot alerts by repository
        for alert in dependabot_alerts:
            repo_name = alert.get('repository', {}).get('name')
            if repo_name:
                if repo_name not in repo_to_dependabot_alerts:
                    repo_to_dependabot_alerts[repo_name] = []
                repo_to_dependabot_alerts[repo_name].append(alert)
        
        # Process secret scanning alerts by repository
        for alert in secret_alerts:
            repo_name = alert.get('repository', {}).get('name')
            if repo_name:
                if repo_name not in repo_to_secret_alerts:
                    repo_to_secret_alerts[repo_name] = []
                repo_to_secret_alerts[repo_name].append(alert)
        
        # Process code scanning alerts by repository
        for alert in code_alerts:
            repo_name = alert.get('repository', {}).get('name')
            if repo_name:
                if repo_name not in repo_to_code_alerts:
                    repo_to_code_alerts[repo_name] = []
                repo_to_code_alerts[repo_name].append(alert)
        
        # Update alert counts
        security_data['alert_counts']['repositories_with_secret_alerts'] = len(repo_to_secret_alerts)
        security_data['alert_counts']['repositories_with_code_alerts'] = len(repo_to_code_alerts)
        security_data['alert_counts']['repositories_with_dependabot_alerts'] = len(repo_to_dependabot_alerts)
        security_data['alert_counts']['total_secret_scanning_alerts'] = len(secret_alerts)
        security_data['alert_counts']['total_code_scanning_alerts'] = len(code_alerts)
        security_data['alert_counts']['total_dependabot_alerts'] = len(dependabot_alerts)
        
        # Counters for aggregation
        secret_types = Counter()
        code_alert_rules = Counter()
        dependabot_packages = Counter()
        dependabot_severities = Counter()
        
        # Process alerts for reporting
        for alert in secret_alerts:
            secret_types[alert.get('secret_type', 'unknown')] += 1
            
        for alert in code_alerts:
            rule = alert.get('rule', {}).get('id', 'unknown')
            code_alert_rules[rule] += 1
            
        for alert in dependabot_alerts:
            package = alert.get('dependency', {}).get('package', {}).get('name', 'unknown')
            severity = alert.get('security_advisory', {}).get('severity', 'unknown')
            dependabot_packages[package] += 1
            dependabot_severities[severity] += 1
        
        # Now process repositories for security features
        for repo in repos:
            repo_name = repo['name']
            is_archived = repo.get('archived', False)
            
            # Track archived repositories
            if is_archived:
                archived_repos_count += 1
            
            # Store repository metadata
            security_data['repository_metadata'][repo_name] = {
                'name': repo_name,
                'url': repo.get('html_url', ''),
                'private': repo.get('private', False),
                'archived': is_archived,
                'created_at': repo.get('created_at', ''),
                'updated_at': repo.get('updated_at', ''),
                'pushed_at': repo.get('pushed_at', '')
            }
            
            logger.info(f"Processing repository {repo_name}... ({repos.index(repo) + 1}/{len(repos)})")
            
            repo_data = {
                'name': repo_name,
                'url': repo['html_url'],
                'private': repo['private'],
                'archived': is_archived,
                'security_features': {},
                'alerts': {
                    'secret_scanning': repo_to_secret_alerts.get(repo_name, []),
                    'code_scanning': repo_to_code_alerts.get(repo_name, []),
                    'dependabot': repo_to_dependabot_alerts.get(repo_name, [])
                }
            }
            
            # Check security features
            security_features = self.github_client.get_repository_security_features(repo_name)
            
            # Add feature status to repo data
            repo_data['security_features'] = {
                'advanced_security': security_features.get('advanced_security', {}).get('status') == 'enabled',
                'secret_scanning': security_features.get('secret_scanning', {}).get('status') == 'enabled',
                'secret_scanning_push_protection': security_features.get('secret_scanning_push_protection', {}).get('status') == 'enabled',
                'vulnerability_alerts': self.check_vulnerability_alerts(repo_name),
                'automated_security_fixes': self.check_automated_security_fixes(repo_name)
            }
            
            # Update org-wide counters
            if repo_data['security_features']['advanced_security']:
                security_data['security_features']['advanced_security_enabled'] += 1
                if is_archived:
                    archived_with_advanced_security += 1
            
            if repo_data['security_features']['secret_scanning']:
                security_data['security_features']['secret_scanning_enabled'] += 1
                if is_archived:
                    archived_with_secret_scanning += 1
            
            if repo_data['security_features']['secret_scanning_push_protection']:
                security_data['security_features']['secret_scanning_push_protection_enabled'] += 1
                if is_archived:
                    archived_with_push_protection += 1
            
            if repo_data['security_features']['vulnerability_alerts']:
                security_data['security_features']['vulnerability_alerts_enabled'] += 1
                if is_archived:
                    archived_with_vuln_alerts += 1
            
            if repo_data['security_features']['automated_security_fixes']:
                security_data['security_features']['automated_security_fixes_enabled'] += 1
                if is_archived:
                    archived_with_auto_fixes += 1
            
            # Track archived repos with alerts
            if is_archived:
                if repo_name in repo_to_secret_alerts:
                    archived_with_secret_alerts += 1
                if repo_name in repo_to_code_alerts:
                    archived_with_code_alerts += 1
                if repo_name in repo_to_dependabot_alerts:
                    archived_with_dependabot_alerts += 1
            
            # Add to repository list
            security_data['repositories'].append(repo_data)
        
        # Update archive stats
        security_data['archived_repositories'] = archived_repos_count
        security_data['active_repositories'] = len(repos) - archived_repos_count
        security_data['archive_stats'] = {
            'archived_with_advanced_security': archived_with_advanced_security,
            'archived_with_secret_scanning': archived_with_secret_scanning,
            'archived_with_push_protection': archived_with_push_protection,
            'archived_with_vulnerability_alerts': archived_with_vuln_alerts,
            'archived_with_auto_fixes': archived_with_auto_fixes,
            'archived_with_secret_alerts': archived_with_secret_alerts,
            'archived_with_code_alerts': archived_with_code_alerts,
            'archived_with_dependabot_alerts': archived_with_dependabot_alerts
        }
        
        # Add aggregated data
        security_data['top_vulnerabilities'] = {
            'secret_types': dict(secret_types.most_common(20)),
            'code_rules': dict(code_alert_rules.most_common(20)),
            'dependabot_packages': dict(dependabot_packages.most_common(20)),
            'dependabot_severities': dict(dependabot_severities)
        }
        
        # Calculate percentages
        total_repos = security_data['total_repositories']
        active_repos = security_data['active_repositories']
        
        if total_repos > 0:
            security_data['security_features']['advanced_security_percentage'] = round(
                (security_data['security_features']['advanced_security_enabled'] / total_repos) * 100, 2
            )
            security_data['security_features']['secret_scanning_percentage'] = round(
                (security_data['security_features']['secret_scanning_enabled'] / total_repos) * 100, 2
            )
            security_data['security_features']['secret_scanning_push_protection_percentage'] = round(
                (security_data['security_features']['secret_scanning_push_protection_enabled'] / total_repos) * 100, 2
            )
            security_data['security_features']['vulnerability_alerts_percentage'] = round(
                (security_data['security_features']['vulnerability_alerts_enabled'] / total_repos) * 100, 2
            )
            security_data['security_features']['automated_security_fixes_percentage'] = round(
                (security_data['security_features']['automated_security_fixes_enabled'] / total_repos) * 100, 2
            )
            
            # Calculate percentages for active repos
            if active_repos > 0:
                active_with_advanced_security = security_data['security_features']['advanced_security_enabled'] - archived_with_advanced_security
                active_with_secret_scanning = security_data['security_features']['secret_scanning_enabled'] - archived_with_secret_scanning
                active_with_push_protection = security_data['security_features']['secret_scanning_push_protection_enabled'] - archived_with_push_protection
                active_with_vuln_alerts = security_data['security_features']['vulnerability_alerts_enabled'] - archived_with_vuln_alerts
                active_with_auto_fixes = security_data['security_features']['automated_security_fixes_enabled'] - archived_with_auto_fixes
                
                security_data['active_security_features'] = {
                    'active_with_advanced_security': active_with_advanced_security,
                    'active_with_secret_scanning': active_with_secret_scanning,
                    'active_with_push_protection': active_with_push_protection,
                    'active_with_vuln_alerts': active_with_vuln_alerts,
                    'active_with_auto_fixes': active_with_auto_fixes,
                    'active_advanced_security_percentage': round((active_with_advanced_security / active_repos) * 100, 2),
                    'active_secret_scanning_percentage': round((active_with_secret_scanning / active_repos) * 100, 2),
                    'active_push_protection_percentage': round((active_with_push_protection / active_repos) * 100, 2),
                    'active_vuln_alerts_percentage': round((active_with_vuln_alerts / active_repos) * 100, 2),
                    'active_auto_fixes_percentage': round((active_with_auto_fixes / active_repos) * 100, 2)
                }
        
        return security_data
        
    def generate_report(self):
        """Generate a report of GitHub security status"""
        logger.info("Analyzing GitHub security status...")
        data = self.scan(self.repo_limit)
        
        # Generate basic report
        logger.info("=" * 50)
        logger.info(f"GitHub Security Status Report for {self.org}")
        logger.info("=" * 50)
        
        # Repository limit info
        if data.get('repo_limit_applied'):
            logger.info(f"Note: Repository limit of {data['repo_limit_applied']} was applied")
        
        # Repository breakdown
        total_repos = data['total_repositories']
        archived_repos = data['archived_repositories']
        active_repos = data['active_repositories']
        
        logger.info("\nRepository Breakdown:")
        logger.info(f"  Total repositories: {total_repos}")
        logger.info(f"  Archived repositories: {archived_repos} ({(archived_repos / total_repos * 100):.1f}% of total)")
        logger.info(f"  Active repositories: {active_repos} ({(active_repos / total_repos * 100):.1f}% of total)")
        
        logger.info("\nSecurity Features (All Repositories):")
        logger.info(f"  Advanced Security enabled: {data['security_features']['advanced_security_enabled']} ({data['security_features'].get('advanced_security_percentage', 0)}%)")
        logger.info(f"  Secret Scanning enabled: {data['security_features']['secret_scanning_enabled']} ({data['security_features'].get('secret_scanning_percentage', 0)}%)")
        logger.info(f"  Secret Scanning Push Protection enabled: {data['security_features']['secret_scanning_push_protection_enabled']} ({data['security_features'].get('secret_scanning_push_protection_percentage', 0)}%)")
        logger.info(f"  Vulnerability Alerts enabled: {data['security_features']['vulnerability_alerts_enabled']} ({data['security_features'].get('vulnerability_alerts_percentage', 0)}%)")
        logger.info(f"  Automated Security Fixes enabled: {data['security_features']['automated_security_fixes_enabled']} ({data['security_features'].get('automated_security_fixes_percentage', 0)}%)")
        
        # Security features for active repositories
        if active_repos > 0 and 'active_security_features' in data:
            logger.info("\nSecurity Features (Active Repositories Only):")
            logger.info(f"  Advanced Security enabled: {data['active_security_features']['active_with_advanced_security']} ({data['active_security_features']['active_advanced_security_percentage']}%)")
            logger.info(f"  Secret Scanning enabled: {data['active_security_features']['active_with_secret_scanning']} ({data['active_security_features']['active_secret_scanning_percentage']}%)")
            logger.info(f"  Secret Scanning Push Protection: {data['active_security_features']['active_with_push_protection']} ({data['active_security_features']['active_push_protection_percentage']}%)")
            logger.info(f"  Vulnerability Alerts enabled: {data['active_security_features']['active_with_vuln_alerts']} ({data['active_security_features']['active_vuln_alerts_percentage']}%)")
            logger.info(f"  Automated Security Fixes: {data['active_security_features']['active_with_auto_fixes']} ({data['active_security_features']['active_auto_fixes_percentage']}%)")
        
        # Security features for archived repositories
        if archived_repos > 0:
            logger.info("\nSecurity Features (Archived Repositories Only):")
            logger.info(f"  Advanced Security enabled: {data['archive_stats']['archived_with_advanced_security']} ({(data['archive_stats']['archived_with_advanced_security'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Secret Scanning enabled: {data['archive_stats']['archived_with_secret_scanning']} ({(data['archive_stats']['archived_with_secret_scanning'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Secret Scanning Push Protection: {data['archive_stats']['archived_with_push_protection']} ({(data['archive_stats']['archived_with_push_protection'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Vulnerability Alerts enabled: {data['archive_stats']['archived_with_vulnerability_alerts']} ({(data['archive_stats']['archived_with_vulnerability_alerts'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Automated Security Fixes: {data['archive_stats']['archived_with_auto_fixes']} ({(data['archive_stats']['archived_with_auto_fixes'] / archived_repos * 100):.1f}%)")
        
        logger.info("\nAlert Summary:")
        logger.info(f"  Repositories with Secret Scanning alerts: {data['alert_counts']['repositories_with_secret_alerts']}")
        logger.info(f"  Repositories with Code Scanning alerts: {data['alert_counts']['repositories_with_code_alerts']}")
        logger.info(f"  Repositories with Dependabot alerts: {data['alert_counts']['repositories_with_dependabot_alerts']}")
        logger.info(f"  Total Secret Scanning alerts: {data['alert_counts']['total_secret_scanning_alerts']}")
        logger.info(f"  Total Code Scanning alerts: {data['alert_counts']['total_code_scanning_alerts']}")
        logger.info(f"  Total Dependabot alerts: {data['alert_counts']['total_dependabot_alerts']}")
        
        # Alerts in archived repositories
        if archived_repos > 0:
            logger.info("\nAlerts in Archived Repositories:")
            logger.info(f"  Archived repositories with Secret Scanning alerts: {data['archive_stats']['archived_with_secret_alerts']} ({(data['archive_stats']['archived_with_secret_alerts'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Archived repositories with Code Scanning alerts: {data['archive_stats']['archived_with_code_alerts']} ({(data['archive_stats']['archived_with_code_alerts'] / archived_repos * 100):.1f}%)")
            logger.info(f"  Archived repositories with Dependabot alerts: {data['archive_stats']['archived_with_dependabot_alerts']} ({(data['archive_stats']['archived_with_dependabot_alerts'] / archived_repos * 100):.1f}%)")
        
        if data['top_vulnerabilities']['secret_types']:
            logger.info("\nTop Secret Types:")
            for secret_type, count in data['top_vulnerabilities']['secret_types'].items():
                logger.info(f"  {secret_type}: {count}")
        
        if data['top_vulnerabilities']['code_rules']:
            logger.info("\nTop Code Scanning Rules:")
            for rule, count in data['top_vulnerabilities']['code_rules'].items():
                logger.info(f"  {rule}: {count}")
        
        if data['top_vulnerabilities']['dependabot_packages']:
            logger.info("\nTop Vulnerable Packages:")
            for package, count in data['top_vulnerabilities']['dependabot_packages'].items():
                logger.info(f"  {package}: {count}")
        
        if data['top_vulnerabilities']['dependabot_severities']:
            logger.info("\nDependabot Alert Severities:")
            for severity, count in data['top_vulnerabilities']['dependabot_severities'].items():
                logger.info(f"  {severity}: {count}")
        
        # Save report
        data = self.save_report(data)
        logger.info(f"Detailed report saved to {data['report_file']['local_path']}")
        if data['report_file']['gcs_path']:
            logger.info(f"Report uploaded to {data['report_file']['gcs_path']}")
            
        return data