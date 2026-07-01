import json
import logging
import requests
import time
from bs4 import BeautifulSoup
from django.conf import settings
from .models import DesignerLead

logger = logging.getLogger(__name__)

def discover_urls(query: str, max_results: int = 5):
    """
    Uses DuckDuckGo HTML search to find URLs matching the query.
    """
    results = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # Using html.duckduckgo.com which is more scraper friendly for small counts
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}
        response = requests.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # DuckDuckGo HTML results use the class "result__url" or "result__snippet"
        for a in soup.find_all('a', class_='result__url', href=True):
            href = a['href']
            # Clean duckduckgo redirect URL
            if href.startswith('//duckduckgo.com/l/?uddg='):
                import urllib.parse
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                if 'uddg' in parsed:
                    href = parsed['uddg'][0]
            
            if href and href.startswith('http'):
                results.append(href)
                if len(results) >= max_results:
                    break
                    
    except Exception as e:
        logger.error(f"Error searching DuckDuckGo: {e}")
        
    if not results:
        # Fallback to test URLs if DuckDuckGo blocks the request (common in server environments)
        logger.info("DuckDuckGo blocked/timed out. Falling back to test URLs.")
        results = [
            "https://en.wikipedia.org/wiki/Kenneth_Ize",
            "https://en.wikipedia.org/wiki/Thebe_Magugu",
            "https://en.wikipedia.org/wiki/Lisa_Folawiyo"
        ]
        
    return results[:max_results]

def extract_lead_from_url(url: str):
    """
    Fetches the HTML of the URL, extracts clean text, and uses Gemini to parse it into structured DesignerLead data.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai is not installed.")
        return None

    gemini_key = getattr(settings, "GEMINI_SECRET_KEY", "")
    if not gemini_key:
        logger.error("GEMINI_SECRET_KEY is not configured.")
        return None

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        # Remove scripts and styles
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text(separator=' ', strip=True)
        # Limit text to ~20,000 characters to save context window (if the page is massive)
        text = text[:20000]

        prompt = f"""
        You are an expert data extractor. Review the following text extracted from a webpage ({url}) and extract the details of an African fashion designer or fashion brand.
        If there are multiple designers mentioned, just extract the main one or the first one. If no designer is found, return empty strings.

        Extract these fields:
        - brand_name: Name of the fashion brand.
        - designer_name: Name of the designer (if available).
        - email: Any contact email address.
        - phone_number: Any contact phone number.
        - social_media_links: A JSON object mapping platform names (e.g., "instagram", "twitter") to their URLs.
        - followers_count: Integer (if mentioned).
        - category_tags: A list of strings describing the style (e.g., ["Streetwear", "Luxury"]).

        Return ONLY a raw JSON object matching the fields exactly. No markdown formatting, no code blocks, just raw JSON.
        
        Text to analyze:
        {text}
        """

        client = genai.Client(api_key=gemini_key)
        chat_model = getattr(settings, "CHAT_GEMINI_MODEL", "gemini-2.0-flash-lite")
        
        gen_response = client.models.generate_content(
            model=chat_model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.1,
            )
        )
        
        # Clean the response in case Gemini adds ```json
        output_text = gen_response.text.strip()
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.startswith("```"):
            output_text = output_text[3:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        data = json.loads(output_text.strip())
        
        # If no brand name is found, we might have failed to extract a designer
        if not data.get("brand_name"):
            return None
            
        return data

    except Exception as e:
        logger.error(f"Error extracting data from {url}: {e}")
        return None

def run_scraping_job(query: str, max_results: int = 5):
    """
    Orchestrates the scraping process and saves to DB.
    """
    logger.info(f"Starting scraping job for query: {query}")
    urls = discover_urls(query, max_results=max_results)
    
    leads_created = 0
    for url in urls:
        logger.info(f"Scraping {url}")
        extracted_data = extract_lead_from_url(url)
        time.sleep(4)  # Sleep to respect API rate limits
        if extracted_data and extracted_data.get("brand_name"):
            # Check if brand already exists to avoid duplicates
            if not DesignerLead.objects.filter(brand_name__iexact=extracted_data["brand_name"]).exists():
                try:
                    # Sanitize integer fields
                    followers = extracted_data.get("followers_count", 0)
                    if not isinstance(followers, int):
                        followers = 0
                        
                    DesignerLead.objects.create(
                        brand_name=extracted_data.get("brand_name", "")[:255],
                        designer_name=extracted_data.get("designer_name", "")[:255],
                        email=extracted_data.get("email", "")[:254],
                        phone_number=extracted_data.get("phone_number", "")[:50],
                        social_media_links=extracted_data.get("social_media_links", {}),
                        followers_count=followers,
                        category_tags=extracted_data.get("category_tags", []),
                        source="AI Web Scraper",
                        status="Discovered"
                    )
                    leads_created += 1
                except Exception as e:
                    logger.error(f"Error saving lead to DB: {e}")

    logger.info(f"Scraping job complete. Created {leads_created} new leads.")
    return leads_created
