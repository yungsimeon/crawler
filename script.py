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
from datetime import datetime

class GoogleSearchAPI:
    def __init__(self, api_key: str, search_engine_id: str):
        self.api_key = api_key
        self.search_engine_id = search_engine_id
        self.base_url = "https://www.googleapis.com/customsearch/v1"

    def search_domain(self, domain: str, num_results: int = 10) -> List[Dict]:
        """
        Search for URLs within a specific domain using Google Custom Search API
        
        Args:
            domain (str): The domain to search for (e.g., 'lovable.app')
            num_results (int): Number of results to return (max 10 per request)
            
        Returns:
            List[Dict]: List of search results containing URLs and metadata
        """
        params = {
            'key': self.api_key,
            'cx': self.search_engine_id,
            'q': f'site:{domain}',
            'num': min(num_results, 10)  # Google API limits to 10 results per request
        }

        try:
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            results = []
            if 'items' in data:
                for item in data['items']:
                    results.append({
                        'url': item['link'],
                        'title': item['title'],
                        'snippet': item.get('snippet', '')
                    })
            
            return results
        except requests.exceptions.RequestException as e:
            print(f"Error making request: {e}")
            return []

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
                return {'url': url, 'error': str(e), 'emails': []}
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
    
    # Save results to JSON file (keeping this as backup)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_file = f'results/crawl_results_{domain}_{timestamp}.json'
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved to {output_file}")

if __name__ == "__main__":
    main()
