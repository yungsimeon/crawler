import json
import requests
from typing import List, Dict, Optional, Set
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import re
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse, urljoin
import platform
from pymongo import MongoClient
from datetime import datetime, timedelta
import calendar

class GoogleSearchAPI:
    def __init__(self, api_key: str, search_engine_id: str):
        self.api_key = api_key
        self.search_engine_id = search_engine_id
        self.base_url = "https://www.googleapis.com/customsearch/v1"
        self.max_results_per_page = 10  # Google's maximum per request
        self.max_total_results = 100    # Google's maximum total results per query

    def _get_date_ranges(self, days_back: int = 365) -> List[tuple]:
        """
        Generate date ranges for searching, going back specified number of days
        
        Args:
            days_back (int): Number of days to go back
            
        Returns:
            List[tuple]: List of (start_date, end_date) tuples in YYYY/MM/DD format
        """
        date_ranges = []
        end_date = datetime.now()
        
        for _ in range(days_back):
            # Each range is a single day
            start_date = end_date
            end_date = start_date
            
            # Format dates for Google's API
            start_str = start_date.strftime('%Y/%m/%d')
            end_str = end_date.strftime('%Y/%m/%d')
            
            date_ranges.append((start_str, end_str))
            
            # Move to the previous day
            end_date = start_date - timedelta(days=1)
        
        return date_ranges

    def _search_with_date_range(self, domain: str, start_date: str, end_date: str, start_index: int = 1) -> List[Dict]:
        """
        Perform a search with a specific date range
        
        Args:
            domain (str): The domain to search for
            start_date (str): Start date in YYYY/MM/DD format
            end_date (str): End date in YYYY/MM/DD format
            start_index (int): The starting index for results
            
        Returns:
            List[Dict]: List of search results
        """
        query = f'site:{domain} after:{start_date} before:{end_date}'
        params = {
            'key': self.api_key,
            'cx': self.search_engine_id,
            'q': query,
            'num': self.max_results_per_page,
            'start': start_index
        }

        try:
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if 'items' not in data:
                return []
            
            return [{
                'url': item['link'],
                'title': item['title'],
                'snippet': item.get('snippet', ''),
                'date_range': f"{start_date} to {end_date}"
            } for item in data['items']]
            
        except requests.exceptions.RequestException as e:
            print(f"Error making request for date range {start_date} to {end_date}: {e}")
            return []

    def search_domain(self, domain: str, days_back: int = 365) -> List[Dict]:
        """
        Search for URLs within a specific domain using daily date ranges
        
        Args:
            domain (str): The domain to search for (e.g., 'lovable.app')
            days_back (int): Number of days to search back
            
        Returns:
            List[Dict]: List of search results containing URLs and metadata
        """
        all_results = []
        seen_urls = set()  # Keep track of unique URLs
        
        # Get date ranges
        date_ranges = self._get_date_ranges(days_back)
        
        print(f"Starting search with {len(date_ranges)} daily date ranges...")
        
        # Try each date range
        for start_date, end_date in date_ranges:
            print(f"\nSearching day: {start_date} to {end_date}")
            start_index = 1
            
            # Get up to 100 results for this date range
            while start_index <= 100:
                results = self._search_with_date_range(domain, start_date, end_date, start_index)
                
                if not results:
                    break
                
                # Add only new, unique URLs
                new_results = 0
                for result in results:
                    if result['url'] not in seen_urls:
                        seen_urls.add(result['url'])
                        all_results.append(result)
                        new_results += 1
                
                print(f"Found {len(results)} results, {new_results} new unique URLs")
                
                # Check if we got fewer results than expected
                if len(results) < self.max_results_per_page:
                    break
                
                start_index += self.max_results_per_page
                time.sleep(1)  # Respect API rate limits
        
        print(f"\nFound {len(all_results)} unique results for domain: {domain}")
        return all_results

class WebCrawler:
    def __init__(self):
        self.email_pattern = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        self.contact_keywords = [
            'contact', 'about', 'team', 'support', 'help', 'reach', 'connect',
            'get in touch', 'email us', 'write to us', 'contact us'
        ]
        self.contact_paths = [
            '/contact', '/about', '/team', '/support', '/help',
            '/contact-us', '/about-us', '/get-in-touch'
        ]
        self.is_aws = self._check_if_aws()

    def _check_if_aws(self) -> bool:
        """Check if running on AWS"""
        try:
            with open('/sys/hypervisor/uuid', 'r') as f:
                return 'ec2' in f.read().lower()
        except:
            return False

    def _get_browser_launch_options(self):
        """Get browser launch options based on environment"""
        options = {
            'headless': True,
            'args': [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--window-size=1920x1080',
            ]
        }
        
        # Add AWS-specific options if running on AWS
        if self.is_aws:
            options['args'].extend([
                '--disable-software-rasterizer',
                '--disable-extensions',
                '--single-process',
            ])
        
        return options

    def find_contact_pages(self, soup: BeautifulSoup, base_url: str) -> Set[str]:
        """Find potential contact page URLs from the current page"""
        contact_urls = set()
        base_domain = urlparse(base_url).netloc
        
        # First check for common contact page paths
        for path in self.contact_paths:
            full_url = urljoin(base_url, path)
            if urlparse(full_url).netloc == base_domain:
                contact_urls.add(full_url)

        # Then check all links on the page
        for link in soup.find_all('a', href=True):
            href = link['href']
            text = link.get_text().lower()
            
            # Check if the link text contains contact-related keywords
            if any(keyword in text for keyword in self.contact_keywords):
                # Handle relative URLs
                if href.startswith('/'):
                    full_url = urljoin(base_url, href)
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = urljoin(base_url, href)
                
                # Only add URLs from the same domain
                if urlparse(full_url).netloc == base_domain:
                    contact_urls.add(full_url)
        
        return contact_urls

    def extract_emails(self, text: str) -> List[str]:
        """Extract email addresses from text"""
        # Find all potential email addresses
        potential_emails = self.email_pattern.findall(text)
        
        # Filter out common false positives
        filtered_emails = []
        for email in potential_emails:
            # Skip common false positives
            if any(x in email.lower() for x in ['example', 'domain', 'email', 'user']):
                continue
            # Skip very short emails (likely false positives)
            if len(email) < 6:
                continue
            filtered_emails.append(email)
        
        return list(set(filtered_emails))

    def crawl_page(self, url: str) -> Dict:
        """
        Crawl a webpage to find contact information
        
        Args:
            url (str): The URL to crawl
            
        Returns:
            Dict: Information found on the page including emails and contact page
        """
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(**self._get_browser_launch_options())
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                )
                page = context.new_page()
                
                # Set a reasonable timeout
                page.set_default_timeout(30000)
                
                # Navigate to the page
                response = page.goto(url)
                if not response:
                    return {'url': url, 'error': 'Failed to load page', 'emails': []}
                
                # Wait for the page to load
                page.wait_for_load_state('networkidle')
                
                # Get the page content
                content = page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                # Extract emails from the current page
                emails = self.extract_emails(content)
                
                # If no emails found, try to find and visit contact pages
                if not emails:
                    contact_urls = self.find_contact_pages(soup, url)
                    visited_urls = set()
                    
                    for contact_url in contact_urls:
                        if contact_url in visited_urls:
                            continue
                            
                        try:
                            print(f"Checking contact page: {contact_url}")
                            response = page.goto(contact_url)
                            if response:
                                page.wait_for_load_state('networkidle')
                                contact_content = page.content()
                                contact_emails = self.extract_emails(contact_content)
                                if contact_emails:
                                    emails.extend(contact_emails)
                                    break  # Stop if we found emails
                                visited_urls.add(contact_url)
                        except Exception as e:
                            print(f"Error visiting contact page {contact_url}: {str(e)}")
                            continue
                
                return {
                    'url': url,
                    'emails': list(set(emails)),  # Remove duplicates
                    'contact_pages_checked': list(visited_urls) if not emails else []
                }
                
            except Exception as e:
                error_msg = str(e)
                if "timeout" in error_msg.lower():
                    return {'url': url, 'error': f'Timeout after 30 seconds: {error_msg}', 'emails': []}
                else:
                    return {'url': url, 'error': error_msg, 'emails': []}
            finally:
                try:
                    context.close()
                    browser.close()
                except:
                    pass

class MongoDBHandler:
    def __init__(self, uri: str):
        self.client = MongoClient(uri)
        self.db = self.client['hydrapatch']
        self.collection = self.db['scraped_sites']

    def save_result(self, result: Dict):
        """
        Save a single crawl result to MongoDB
        
        Args:
            result (Dict): Single crawl result to save
        """
        url = result['url']
        crawl_result = result['crawl_result']
        
        # Skip if there was an error or no emails found
        if 'error' in crawl_result or not crawl_result['emails']:
            return
            
        # Create document for each email found
        for email in crawl_result['emails']:
            document = {
                'url': url,
                'email': email,
                'crawled_at': datetime.utcnow()
            }
            try:
                # Use upsert to avoid duplicates
                self.collection.update_one(
                    {'url': document['url'], 'email': document['email']},
                    {'$set': document},
                    upsert=True
                )
                print(f"Saved to MongoDB: {email} from {url}")
            except Exception as e:
                print(f"Error saving to MongoDB: {str(e)}")

def load_input_config() -> Dict:
    """
    Load configuration from input.json file
    
    Returns:
        Dict: Configuration containing domain and other parameters
    """
    try:
        with open('input.json', 'r') as f:
            config = json.load(f)
            if 'domain' not in config:
                raise ValueError("input.json must contain a 'domain' field")
            return config
    except FileNotFoundError:
        print("Error: input.json file not found")
        exit(1)
    except json.JSONDecodeError:
        print("Error: input.json is not valid JSON")
        exit(1)
    except ValueError as e:
        print(f"Error: {str(e)}")
        exit(1)

def main():
    # Load environment variables
    load_dotenv()
    
    # Get API credentials from environment variables
    api_key = os.getenv('GOOGLE_SEARCH_API_KEY')
    search_engine_id = os.getenv('GOOGLE_SEARCH_ENGINE_ID')
    mongodb_uri = os.getenv('MONGODB_URI')
    
    if not all([api_key, search_engine_id, mongodb_uri]):
        print("Error: Please set all required environment variables (GOOGLE_SEARCH_API_KEY, GOOGLE_SEARCH_ENGINE_ID, MONGODB_URI)")
        return

    # Initialize the search API and crawler
    search_api = GoogleSearchAPI(api_key, search_engine_id)
    crawler = WebCrawler()
    mongo_handler = MongoDBHandler(mongodb_uri)
    
    # Load configuration from input.json
    config = load_input_config()
    domain = config['domain']

    # Perform search
    results = search_api.search_domain(domain)
    
    # Create output directory if it doesn't exist
    os.makedirs('results', exist_ok=True)
    
    # Prepare results for saving
    all_results = []
    
    # Crawl each result
    print(f"\nSearching for contact information on {domain}...")
    print("-" * 50)
    
    for result in results:
        print(f"\nCrawling: {result['url']}")
        crawl_result = crawler.crawl_page(result['url'])
        
        # Create result object
        processed_result = {
            'url': result['url'],
            'crawl_result': crawl_result
        }
        
        # Add to results list for JSON backup
        all_results.append(processed_result)
        
        # Save to MongoDB immediately
        mongo_handler.save_result(processed_result)
        
        if 'error' in crawl_result:
            print(f"Error: {crawl_result['error']}")
        else:
            if crawl_result['emails']:
                print("Found emails:")
                for email in crawl_result['emails']:
                    print(f"- {email}")
            else:
                print("No emails found")
                if crawl_result['contact_pages_checked']:
                    print(f"Contact pages checked: {', '.join(crawl_result['contact_pages_checked'])}")
        
        print("-" * 50)
        # Add a small delay between requests to be polite
        time.sleep(2)
    


if __name__ == "__main__":
    main()
