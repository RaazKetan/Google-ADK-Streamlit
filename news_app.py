import os
import asyncio
import feedparser
from typing import List, Dict, Any, Optional
import streamlit as st
import re
import time
import warnings
import logging
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

# Google ADK Components
from google.adk.agents import Agent
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.adk.tools.tool_context import ToolContext # For accessing session state in tools
from google.genai import types as genai_types # For formatting messages for ADK
# --- Configuration ---
load_dotenv() # Load environment variables from .env file (specifically GOOGLE_API_KEY)
warnings.filterwarnings("ignore") # Optional: Suppress common warnings during development
logging.basicConfig(level=logging.ERROR) # Configure logging level (ERROR hides most ADK logs, INFO shows more detail)
# --- Constants ---
# Define keys for accessing data stored in the ADK session state
NEWS_ITEMS_PRESENTED_STATE_KEY = "last_presented_news_items" # Stores the list of news shown in the last briefing
NEWS_FETCH_CACHE_STATE_KEY = "fetched_news_bbc_npr_cache"  # Stores fetched feed data & caching info (etag/modified)
# Define the default RSS feed URLs to query
DEFAULT_FEED_URLS = [
    "https://feeds.bbci.co.uk/news/rss.xml", # BBC News World Edition
    "https://feeds.npr.org/1001/rss.xml"  # NPR News Headlines
]
# Set a limit on the total number of items processed from feeds to prevent excessive processing time
MAX_ITEMS_TO_PROCESS = 200 # Adjust as needed
# Define the Gemini model and unique identifiers for the ADK application and user session
MODEL_GEMINI = "gemini-2.0-flash" # Specify the LLM for the agent
APP_NAME_FOR_ADK = "news_agent_final_mem_v2" # Unique name for this ADK application runner
USER_ID = "streamlit_user_sf_final_mem_v2" # Unique ID representing the user in this Streamlit app
print("✅ Imports and Configuration Loaded.")
# --------------------------------------------------------------------------
# Tool Function Definition (Fetches news, filters, manages state)
# --------------------------------------------------------------------------
def fetch_and_return_news(tool_context: ToolContext, target_date_str: Optional[str] = None) -> Dict[str, Any]:
    """
    Fetches news from BBC & NPR RSS feeds using feedparser.
    Filters news items based on the provided target_date_str ('today', 'yesterday', 'YYYY-MM-DD')
    or defaults to the last 7 days if no date is specified.
    Uses etag and modified headers for feed caching via feedparser.
    Stores ALL fetched/cached items and feed etag/modified data in session state['fetched_news_bbc_npr_cache'].
    Stores only the FILTERED items that are returned to the agent in session state['last_presented_news_items']
    for potential follow-up questions. Clears this state if no items match the filter.
    Args:
        tool_context: The ADK ToolContext, providing access to session state via tool_context.state.
        target_date_str: Optional string representing the target date or 'today'/'yesterday'.
    Returns:
        A dictionary with 'status' ('success' or 'error') and either 'items' (list of news dicts)
        or 'message' (error description).
    """
    print(f"--- Tool: fetch_and_return_news called ---")
    print(f"--- Tool: Received target_date_str argument: {target_date_str} ---")
    target_urls = DEFAULT_FEED_URLS
    # --- Determine Target Date(s) for Filtering ---
    today = date.today()
    date_start = None # Start of date range for filtering
    date_end = None   # End of date range for filtering
    single_day = None # Flag/date if user requests a specific day
    # Parse the user-provided date string (case-insensitive)
    if target_date_str:
        target_date_str_lower = target_date_str.lower()
        if target_date_str_lower == "today":
            single_day = today
        elif target_date_str_lower == "yesterday":
            single_day = today - timedelta(days=1)
        else:
            # Attempt to parse YYYY-MM-DD format
            try:
                parsed_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
                # Add a check to prevent requests for very old dates (RSS feeds have limited history)
                if (today - parsed_date).days > 14: # Check if older than 2 weeks
                    msg = "Sorry, I can only retrieve news from the past two weeks. Please provide a more recent date (e.g., 'today', 'yesterday', or YYYY-MM-DD within the last 14 days)."
                    print(f"--- Tool: Returning error - Date too old: {parsed_date} ---")
                    # Clear any potentially stale "presented" items if the date is invalid
                    if NEWS_ITEMS_PRESENTED_STATE_KEY in tool_context.state:
                        del tool_context.state[NEWS_ITEMS_PRESENTED_STATE_KEY]
                    return {"status": "error", "message": msg}
                single_day = parsed_date
            except ValueError:
                # Handle cases where the date string is not in the expected format
                err_msg = f"Sorry, I couldn't understand the date '{target_date_str}'. Please use the format YYYY-MM-DD, or the words 'today' or 'yesterday'."
                print(f"--- Tool: ERROR - Invalid date format provided: {target_date_str} ---")
                if NEWS_ITEMS_PRESENTED_STATE_KEY in tool_context.state:
                    del tool_context.state[NEWS_ITEMS_PRESENTED_STATE_KEY]
                return {"status": "error", "message": err_msg}
        print(f"--- Tool: Filtering for single target day: {single_day} ---")
    else:
        # Default behavior: If no date is specified by the user, filter for the last 7 days
        date_end = today
        date_start = today - timedelta(days=7)
        print(f"--- Tool: No date specified, defaulting to filter range: {date_start} to {date_end} ---")
    # --- Fetching Logic with Caching ---
    all_fetched_or_cached_items: List[Dict[str, Any]] = [] # List to hold items from all feeds (before filtering)
    errors: List[str] = [] # List to collect any errors encountered during fetching
    item_fetch_count = 0 # Counter to ensure we don't process too many items
    # **ADK State Management**: Access or initialize the cache state using ToolContext
    # tool_context.state is a dictionary managed by ADK for the current session.
    if NEWS_FETCH_CACHE_STATE_KEY not in tool_context.state or not isinstance(tool_context.state.get(NEWS_FETCH_CACHE_STATE_KEY), dict):
        # Initialize the state if it doesn't exist or has an incorrect type
        tool_context.state[NEWS_FETCH_CACHE_STATE_KEY] = {'items': [], 'cache': {}}
        print(f"--- Tool: Initialized fetch cache state['{NEWS_FETCH_CACHE_STATE_KEY}'] ---")
    fetch_state = tool_context.state[NEWS_FETCH_CACHE_STATE_KEY]
    # Ensure the expected sub-keys 'items' and 'cache' exist
    if 'cache' not in fetch_state: fetch_state['cache'] = {}
    if 'items' not in fetch_state: fetch_state['items'] = []
    current_cache = fetch_state.get('cache', {}) # Contains {'url': {'etag': '...', 'modified': '...'}}
    new_cache_updates: Dict[str, Dict[str, str]] = {} # Temporarily store updates to the cache info
    print(f"--- Tool: Starting fetch loop for {len(target_urls)} URLs. Item processing limit: {MAX_ITEMS_TO_PROCESS} ---")
    for url in target_urls:
        # Check if we've hit the processing limit
        if item_fetch_count >= MAX_ITEMS_TO_PROCESS:
            print(f"--- Tool: Reached MAX_ITEMS_TO_PROCESS ({MAX_ITEMS_TO_PROCESS}). Stopping feed fetch loop. ---")
            break
        try:
            # Retrieve ETag and Last-Modified data for this URL from our state cache
            feed_cache = current_cache.get(url, {})
            etag = feed_cache.get('etag')
            modified = feed_cache.get('modified')
            # **Feedparser Caching**: Provide cached etag/modified values to feedparser.
            # If the server responds with HTTP 304, feedparser indicates this via feed.status.
            print(f"--- Tool: Parsing '{url}' - Using cached (ETag: {etag}, Modified: {modified}) ---")
            feed = feedparser.parse(url, etag=etag, modified=modified)
            print(f"--- DEBUG: feed type = {type(feed)} ---")
            print(f"--- DEBUG: feed keys = {list(feed.keys())} ---")
            print(f"--- DEBUG: feed = {feed} ---")
            status = getattr(feed, 'status', None)
            # --- Handle Feedparser Response Status ---
            if status == 304: # HTTP 304 Not Modified
                # Feed content hasn't changed since last fetch. Reuse items stored in our state cache.
                cached_feed_items = [item for item in fetch_state.get('items', []) if item.get('source_feed') == url]
                all_fetched_or_cached_items.extend(cached_feed_items)
                item_fetch_count += len(cached_feed_items) # Count these towards the limit
                print(f"--- Tool: Feed '{url}' returned 304 Not Modified. Reusing {len(cached_feed_items)} cached items from state. ---")
                continue # Skip processing entries for this feed, move to the next URL
            elif isinstance(status, int) and status >= 400: # Handle HTTP errors (e.g., 404 Not Found, 500 Server Error)
                err = f"Failed to fetch '{url}', server responded with HTTP status: {status}"
                errors.append(err)
                print(f"--- Tool: ERROR fetching feed - {err} ---")
                continue # Move to the next feed URL
            # Check if feedparser encountered issues parsing the feed content (even if HTTP status was OK)
            if feed.bozo:
                bozo_msg = getattr(feed, 'bozo_exception', 'Unknown parsing issue')
                print(f"--- Tool: Warning - Feed '{url}' may be ill-formed (bozo detected): {bozo_msg} ---")
                # Continue processing entries even if bozo is set, as some data might still be valid
            # --- Process Feed Entries ---
            if feed.entries:
                print(f"--- Tool: Feed '{url}' fetched successfully (Status: {status}). Processing {len(feed.entries)} entries. ---")
                # Prepare to store new ETag/Modified data if the server provided them
                url_cache_update = {}
                if hasattr(feed, 'etag') and feed.etag:
                    url_cache_update['etag'] = feed.etag
                if hasattr(feed, 'modified') and feed.modified:
                    url_cache_update['modified'] = feed.modified
                if url_cache_update:
                    new_cache_updates[url] = url_cache_update # Store update for later
                # Iterate through each news item (entry) in the feed
                for entry in feed.entries:
                    # Check processing limit within the inner loop as well
                    if item_fetch_count >= MAX_ITEMS_TO_PROCESS: break
                    # Ensure essential fields (title, link) are present
                    if hasattr(entry, 'title') and hasattr(entry, 'link'):
                        # Determine the most relevant publication/update time using feedparser's parsed structs
                        published_struct = getattr(entry, 'published_parsed', None) # time.struct_time or None
                        updated_struct = getattr(entry, 'updated_parsed', None)   # time.struct_time or None
                        relevant_struct = updated_struct or published_struct # Prefer updated time for filtering
                        # Extract and clean the description/summary (remove HTML tags)
                        description_text = getattr(entry, 'description', '')
                        try:
                            description_text = re.sub('<[^<]+?>', '', description_text).strip()
                        except Exception as regex_err:
                             print(f"--- Tool: Warning - Regex error cleaning description for entry '{entry.title}': {regex_err} ---")
                             description_text = getattr(entry, 'description', '') # Fallback to raw description
                        # Extract and clean the main content (often in entry.content list)
                        content_text = ""
                        if hasattr(entry, 'content') and isinstance(entry.content, list):
                            combined_content_pieces = []
                            for content_item in entry.content:
                                # Content items are often dicts with 'value' and 'type'
                                if isinstance(content_item, dict) and 'value' in content_item:
                                    # Clean HTML from the content value
                                    plain_content = re.sub('<[^<]+?>', '', content_item.get('value', '')).strip()
                                    if plain_content: # Add only if there's actual text after cleaning
                                        combined_content_pieces.append(plain_content)
                            content_text = "\n\n".join(combined_content_pieces) # Combine cleaned pieces
                        # Attempt to extract an image URL (handling common RSS patterns)
                        image_url = None
                        # Check standard media:thumbnail (used by BBC)
                        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                            if isinstance(entry.media_thumbnail, list) and len(entry.media_thumbnail) > 0:
                                image_url = entry.media_thumbnail[0].get('url')
                        # Check if image is embedded in HTML content (used by NPR)
                        elif not image_url and hasattr(entry, 'content'): # Only check if not found above
                            if isinstance(entry.content, list) and len(entry.content) > 0:
                                # Find the first HTML content part
                                html_content = next((c.get('value') for c in entry.content if isinstance(c, dict) and 'html' in c.get('type', '')), None)
                                if html_content:
                                    # Simple regex to find the first <img> tag's src attribute
                                    match = re.search(r"<img\s+[^>]*src=['\"]([^'\"]+)['\"]", html_content, re.IGNORECASE)
                                    if match:
                                        image_url = match.group(1)
                        # Compile the structured data for this news item
                        item_data = {
                            "title": entry.title,
                            "link": entry.link,
                            "published_str": getattr(entry, 'published', ''), # Store original published string if available
                            "published_or_updated_struct": relevant_struct, # Temporarily store struct for date filtering
                            "source_feed": url, # Track the origin feed
                            "image_url": image_url, # May be None
                            "description": description_text, # Cleaned summary
                            "content": content_text # Cleaned main content (can be empty)
                        }
                        all_fetched_or_cached_items.append(item_data)
                        item_fetch_count += 1 # Increment item counter
            else:
                 # Log if a feed was fetched successfully but contained no entries
                 print(f"--- Tool: No entries found in fetched feed '{url}' (Status: {status}). ---")
        except Exception as e:
            # Catch any other unexpected errors during the processing of a single feed
            error_msg = f"Unexpected error processing feed '{url}': {e}"
            print(f"--- Tool: ERROR - {error_msg} ---")
            errors.append(error_msg)
            logging.exception(f"Error details processing feed {url}:") # Log the full traceback
    print(f"--- Tool: Feed fetch loop complete. Total items accumulated: {len(all_fetched_or_cached_items)}. Fetch/Parse errors: {len(errors)} ---")
    # --- Filtering Logic (Apply date criteria to the accumulated items) ---
    filtered_items: List[Dict[str, Any]] = [] # List to hold items that match the date filter
    if single_day:
        # Filter for items published/updated on the specific 'single_day'
        for item in all_fetched_or_cached_items:
            struct_to_check = item.get("published_or_updated_struct") # Get the stored time.struct_time
            if struct_to_check:
                try:
                    # Convert the time.struct_time to a standard date object for comparison
                    item_date = date(struct_to_check.tm_year, struct_to_check.tm_mon, struct_to_check.tm_mday)
                    if item_date == single_day:
                        # If date matches, remove the temporary struct before adding to the result list
                        item.pop("published_or_updated_struct", None)
                        filtered_items.append(item)
                except (ValueError, AttributeError, TypeError) as date_err:
                    # Ignore items with invalid or missing date structures
                    # print(f"--- Tool: Warning - Skipping item due to invalid date structure: {item.get('title')} Error: {date_err} ---")
                    pass # Continue to next item
        print(f"--- Tool: Filtering complete for single day {single_day}. Matched items: {len(filtered_items)}. ---")
    elif date_start and date_end:
        # Filter for items within the 'date_start' to 'date_end' range (default case)
        for item in all_fetched_or_cached_items:
            struct_to_check = item.get("published_or_updated_struct")
            if struct_to_check:
                try:
                    item_date = date(struct_to_check.tm_year, struct_to_check.tm_mon, struct_to_check.tm_mday)
                    # Check if the item's date falls within the calculated range (inclusive)
                    if date_start <= item_date <= date_end:
                        item.pop("published_or_updated_struct", None)
                        filtered_items.append(item)
                except (ValueError, AttributeError, TypeError) as date_err:
                    # print(f"--- Tool: Warning - Skipping item due to invalid date structure: {item.get('title')} Error: {date_err} ---")
                    pass # Continue to next item
        print(f"--- Tool: Filtering complete for date range {date_start} to {date_end}. Matched items: {len(filtered_items)}. ---")
    else:
         # Fallback if date logic failed (shouldn't happen with current structure)
         print(f"--- Tool: Warning - No valid date filter (single day or range) determined. Returning all accumulated items. ---")
         filtered_items = all_fetched_or_cached_items # Pass through all items
         # Ensure struct is removed even in fallback
         for item in filtered_items: item.pop("published_or_updated_struct", None)

    # --- Update ADK Session State ---
    # **State Management**: Update the fetch cache state within tool_context.state
    # This ensures that subsequent calls within the same session can reuse cached items/etags.
    if all_fetched_or_cached_items or new_cache_updates: # Only update state if there's new data or cache info
        fetch_state['items'] = all_fetched_or_cached_items # Store the complete list (needed for 304 cache hits)
        if new_cache_updates:
            fetch_state['cache'].update(new_cache_updates) # Apply collected ETag/Modified updates
        # NOTE: We are modifying the 'fetch_state' dictionary obtained from tool_context.state directly.
        # ADK handles persisting these changes to the session state implicitly.
        print(f"--- Tool: Updated fetch cache in state['{NEWS_FETCH_CACHE_STATE_KEY}']. Cache entries: {len(fetch_state['cache'])}, Total items stored: {len(fetch_state['items'])}. ---")
    # **State Management**: Update the "presented items" state used for follow-up questions.
    # This state stores ONLY the items that matched the filter and will be returned to the agent.
    if filtered_items:
        # Store the list of filtered items. This overwrites any previous list.
        tool_context.state[NEWS_ITEMS_PRESENTED_STATE_KEY] = filtered_items
        print(f"--- Tool: Stored {len(filtered_items)} filtered items in state['{NEWS_ITEMS_PRESENTED_STATE_KEY}'] for agent follow-up. ---")
    else:
        # If filtering resulted in an empty list, explicitly clear the "presented" state.
        # This prevents the agent from incorrectly answering follow-ups based on an old briefing.
        if NEWS_ITEMS_PRESENTED_STATE_KEY in tool_context.state:
            del tool_context.state[NEWS_ITEMS_PRESENTED_STATE_KEY]
            print(f"--- Tool: Cleared state['{NEWS_ITEMS_PRESENTED_STATE_KEY}'] as no items matched the filter criteria. ---")
    # --- Prepare and Return Result Dictionary to the Agent ---
    if filtered_items:
        # Success case: Return the list of filtered news items
        return {"status": "success", "items": filtered_items}
    elif errors:
        # Errors occurred during fetching, report them
        return {"status": "error", "message": f"Sorry, I encountered problems while trying to fetch news from some sources. Errors: {'; '.join(errors)}"}
    else:
        # Fetching was successful (no errors), but filtering yielded no results for the specified date/range
        msg = "I looked for news for the requested period, but couldn't find any items from BBC or NPR in the feeds. You could try 'today', 'yesterday', or a different recent date (YYYY-MM-DD)."
        return {"status": "error", "message": msg}
print("✅ Tool 'fetch_and_return_news' defined.")

# --------------------------------------------------------------------------
# ADK Agent Definition (Instructions emphasize state usage for follow-ups)
# --------------------------------------------------------------------------
root_agent = Agent(
    name="news_and_chat_agent_final_mem_v2", # Unique name for this agent configuration
    model=MODEL_GEMINI, # The LLM that will interpret instructions and user input
    description="A helpful assistant that provides news briefings from BBC/NPR based on specified dates and can answer follow-up questions about the most recent briefing using conversational memory. Also handles general chat.",
    # The instruction prompt is critical for defining the agent's reasoning process.
    instruction=(
        "**Your Role:** You are a helpful News & Chat Assistant.\n\n"
        "**Core Task Flow (Follow PRECISELY):**\n"
        "1.  **Analyze User's LATEST Message:** Determine the primary intent:\n"
        "    *   **Intent A: Requesting a NEW News Briefing:** Keywords like 'news', 'headlines', 'briefing', 'what happened', combined with date specifiers ('today', 'yesterday', 'YYYY-MM-DD') or lack thereof (implies default range).\n"
        "    *   **Intent B: Follow-up on the IMMEDIATELY Preceding Briefing:** Questions referring *directly* to news items *you just presented* (e.g., 'tell me more about the first story', 'what link was that?', 'expand on the BBC item'). Context is key.\n"
        "    *   **Intent C: General Conversation:** Anything else - greetings, questions unrelated to news briefings, statements.\n\n"
        "2.  **Select Action based ONLY on the Determined Intent:**\n"
        "    *   **If Intent is B (Follow-up):**\n"
        "        - **>>> DO NOT CALL ANY TOOLS. <<<**\n"
        "        - **Access session state:** Retrieve the list of news items stored under the key `last_presented_news_items`.\n"
        "        - **Answer SOLELY from state:** Formulate your response based *only* on the information (title, description, content, link) within the items found in that state variable.\n"
        "        - **Handle Missing State/Info:** If the state key is empty, or the specific information requested isn't in the stored items, explicitly state that you don't have the details from the last briefing available (e.g., 'I don't have the specifics of that item from the previous briefing anymore.'). Do not guess or call the tool.\n\n"
        "    *   **If Intent is A (New Briefing):**\n"
        "        - **>>> MUST call the `fetch_and_return_news` tool. <<<**\n"
        "        - **Determine `target_date_str` argument:** If the user specified 'today', 'yesterday', or 'YYYY-MM-DD', pass that exact string as the `target_date_str` argument to the tool. If the user did *not* specify a date (e.g., 'latest news'), call the tool with `target_date_str=None` (or simply omit the argument) to trigger the tool's default 7-day range behavior.\n"
        "        - **Process Tool Result:** Wait for the tool to return a dictionary and handle it according to the 'Handling Tool Results' section below.\n\n"
        "    *   **If Intent is C (General Conversation):**\n"
        "        - **>>> DO NOT CALL ANY TOOLS. <<<**\n"
        "        - Respond conversationally as a helpful AI assistant.\n\n"
        "**Handling Tool Results (Applies ONLY after calling `fetch_and_return_news`):**\n"
        "-   **On Tool Error:** If the tool returns `{'status': 'error', 'message': '...'}`: Relay the exact error 'message' from the tool to the user.\n"
        "-   **On Tool Success (with items):** If the tool returns `{'status': 'success', 'items': [...]}` and the 'items' list is NOT empty:\n"
        "    *   Clearly state the date/range covered (e.g., 'Here's the news for YYYY-MM-DD:', 'Here are headlines from the past week:').\n"
        "    *   Present *all* items from the 'items' list.\n"
        "    *   Format each item using Markdown for readability:\n"
        "        ```markdown\n"
        "        ### [Title](link)\n"
        "        **Source:** [BBC News or NPR News - determine from source_feed URL]\n"
        "        **Published:** [published_str, if available]\n\n"
        "        [description]\n\n" # Display the summary/description first
        "        [content if available and provides additional info beyond description]\n" # Display longer content if useful
        "        ---\n" # Use a separator
        "        ```\n"
        "    *   Identify the source (BBC or NPR) clearly for each item.\n"
        "    *   **Crucially: Do not mention or attempt to display image URLs.**\n"
        "-   **On Tool Success (no items):** If the tool returns `{'status': 'success', 'items': []}`: Inform the user clearly that no news items were found for their request (e.g., 'I checked the feeds, but couldn't find any news items for that specific date/period.').\n\n"
        "**General Behavior:** Use Markdown for all responses. Be accurate based on the information provided (either from the tool or state). Do not invent information."
    ),
    # Make the Python function available to the agent
    tools=[
        fetch_and_return_news
    ]
)
print("✅ ADK Agent 'root_agent' defined.")

# --------------------------------------------------------------------------
# ADK Initialization and Runner Helper Functions
# --------------------------------------------------------------------------
# Use Streamlit's caching for resources (@st.cache_resource). This ensures that
# the ADK Runner and SessionService are initialized only once per user's
# browser session, maintaining state continuity across Streamlit script reruns.
@st.cache_resource
def initialize_adk():
    """
    Initializes the ADK Runner and InMemorySessionService for the application.
    Manages the unique ADK session ID within the Streamlit session state.
    Returns:
        tuple: (Runner instance, active ADK session ID)
    """
    print("--- ADK Init: Attempting to initialize Runner and Session Service... ---")
    # InMemorySessionService stores all session data (history, state dictionaries)
    # in the RAM of the process running the Streamlit app. Data is lost if the
    # Python process stops. For persistent storage, explore DatabaseSessionService.
    session_service = InMemorySessionService()
    print(f"--- ADK Init: InMemorySessionService instantiated. ---")
    # The Runner connects the Agent definition with the SessionService to handle
    # the execution flow for each user interaction.
    runner = Runner(
        agent=root_agent,           # The agent configuration defined above
        app_name=APP_NAME_FOR_ADK,  # Identifier for this runner instance
        session_service=session_service # Service used to load/save session data
    )
    print(f"--- ADK Init: Runner instantiated for agent '{root_agent.name}'. ---")
    # We need a persistent session ID for the ADK conversation within the context
    # of a single user's interaction with the Streamlit app. We store this ID
    # in Streamlit's own session state (`st.session_state`).
    adk_session_key = 'adk_session_id_final_mem_v2' # Unique key within st.session_state
    if adk_session_key not in st.session_state:
        # If this is the first time this Streamlit session is running initialize_adk,
        # generate a new, unique session ID for the ADK conversation.
        session_id = f"streamlit_session_final_mem_v2_{int(time.time())}_{os.urandom(4).hex()}"
        st.session_state[adk_session_key] = session_id # Store the new ID in Streamlit's state
        print(f"--- ADK Init: Generated new ADK session ID: {session_id} ---")
        try:
            # Create the corresponding session record within the ADK SessionService.
            # This session starts with an empty state dictionary `{}`.
            session_service.create_session(
                app_name=APP_NAME_FOR_ADK,
                user_id=USER_ID,
                session_id=session_id,
                state={} # Initialize with an empty state
            )
            print(f"--- ADK Init: Successfully created new session in ADK SessionService. ---")
        except Exception as e:
            # Log and re-raise errors during initial session creation
            print(f"--- ADK Init: FATAL ERROR - Could not create initial session in ADK SessionService: {e} ---")
            logging.exception("ADK Session Service create_session failed:")
            raise # Stop execution if the session can't be created
    else:
        # If adk_session_key exists in st.session_state, reuse the existing ADK session ID.
        session_id = st.session_state[adk_session_key]
        print(f"--- ADK Init: Reusing existing ADK session ID from Streamlit state: {session_id} ---")
        # **Important Check for InMemorySessionService**:
        # Since InMemorySessionService loses data if the script restarts (e.g., code change, server reboot),
        # we must verify if the session *actually* still exists in the service's memory.
        if not session_service.get_session(app_name=APP_NAME_FOR_ADK, user_id=USER_ID, session_id=session_id):
            print(f"--- ADK Init: WARNING - Session {session_id} not found in InMemorySessionService memory (likely due to script restart). Recreating session. State will be lost. ---")
            try:
                # Recreate the session record in the service. The state will be reset to empty.
                session_service.create_session(
                    app_name=APP_NAME_FOR_ADK,
                    user_id=USER_ID,
                    session_id=session_id,
                    state={} # Recreated session starts with empty state
                )
            except Exception as e:
                # Handle errors during recreation attempt
                print(f"--- ADK Init: ERROR - Could not recreate missing session {session_id} in ADK SessionService: {e} ---")
                logging.exception("ADK Session Service recreation failed:")
                # Depending on requirements, you might raise an error here or allow proceeding with a potentially inconsistent state.
    print(f"--- ADK Init: Initialization sequence complete. Runner is ready. Active Session ID: {session_id} ---")
    # Return the configured runner and the session ID to be used for interactions
    return runner, session_id
async def run_adk_async(runner: Runner, session_id: str, user_message_text: str) -> str:
    """
    Asynchronously executes one turn of the ADK agent conversation.
    Args:
        runner: The initialized ADK Runner.
        session_id: The current ADK session ID.
        user_message_text: The text input from the user for this turn.
    Returns:
        The agent's final text response as a string.
    """
    print(f"\n--- ADK Run: Starting async execution for session {session_id} ---")
    print(f"--- ADK Run: Processing User Query (truncated): '{user_message_text[:150]}...' ---")
    # Format the user's message into the google.genai.types.Content structure required by ADK runner.
    content = genai_types.Content(
        role='user', # Standard role identifier for user input
        parts=[genai_types.Part(text=user_message_text)] # The actual text content
    )
    final_response_text = "[Agent encountered an issue and did not produce a final response]" # Default error message
    start_time = time.time() # Start timing the agent execution
    try:
        # The core ADK interaction: runner.run_async processes the new message within the session context.
        # It's an async generator, yielding Event objects that represent stages of the agent's turn
        # (e.g., planning, tool call request, tool result received, LLM response chunk, final response).
        async for event in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=content):
            # In this simple UI, we only need the agent's final output for the turn.
            # The `is_final_response()` method on the event identifies this.
            if event.is_final_response():
                print(f"--- ADK Run: Final response event received. ---")
                # Safely extract the text from the final event's content.
                # The content structure is Content -> parts (list) -> Part -> text.
                if event.content and event.content.parts and hasattr(event.content.parts[0], 'text'):
                    final_response_text = event.content.parts[0].text
                else:
                    # Handle cases where the final event might not contain standard text
                    # (e.g., an error occurred, or the agent structure is different).
                    final_response_text = "[Agent finished but produced no text output]"
                    print(f"--- ADK Run: WARNING - Final event received, but no text content found. Event: {event} ---")
                break # Stop iterating through events once the final response is captured
            # --- Optional: Inspecting Intermediate Events ---
            # else:
            #     # You could log or handle other event types here for debugging or advanced UIs
            #     event_type = type(event).__name__
            #     author = getattr(event, 'author', 'N/A')
            #     print(f"--- ADK Run: Intermediate event received - Type: {event_type}, Author: {author} ---")
            #     # Example: Check for tool calls
            #     if hasattr(event, 'actions') and event.actions and hasattr(event.actions, 'function_call') and event.actions.function_call:
            #          print(f"--- ADK Run: -> Tool call requested: {event.actions.function_call.name} ---")
            #     # Example: Check for tool responses being processed
            #     if hasattr(event, 'actions') and event.actions and hasattr(event.actions, 'function_response') and event.actions.function_response:
            #          print(f"--- ADK Run: -> Processing tool response for ID: {event.actions.function_response.id} ---")
    except Exception as e:
        # Catch any exceptions that occur during the runner.run_async execution.
        print(f"--- ADK Run: !! EXCEPTION during agent execution: {e} !! ---")
        logging.exception("ADK runner.run_async failed:") # Log the full traceback
        # Provide a user-friendly error message
        final_response_text = f"Sorry, an error occurred while processing your request. Please check the logs or try again later. (Error: {e})"
    # Calculate and log the duration of the agent's turn
    end_time = time.time()
    duration = end_time - start_time
    print(f"--- ADK Run: Turn execution completed in {duration:.2f} seconds. ---")
    print(f"--- ADK Run: Final Response (truncated): '{final_response_text[:150]}...' ---")
    # Return the captured final response text
    return final_response_text
# Since Streamlit's main execution flow is synchronous, we need a helper
# function to call our asynchronous `run_adk_async` function.
def run_adk_sync(runner: Runner, session_id: str, user_message_text: str) -> str:
    """
    Synchronous wrapper that executes the asynchronous run_adk_async function.
    Uses asyncio.run() to manage the event loop.
    """
    # asyncio.run() creates a new event loop, runs the provided coroutine until
    # it completes, and then closes the event loop.
    return asyncio.run(run_adk_async(runner, session_id, user_message_text))
print("✅ ADK Runner initialization and helper functions defined.")
# --------------------------------------------------------------------------
# Streamlit User Interface Setup
# --------------------------------------------------------------------------
st.set_page_config(
    page_title="ADK News & Chat Agent",
    layout="wide", # Use wide layout for more space
    initial_sidebar_state="auto" # Keep sidebar visible initially
)
st.title("📰 News & Chat Assistant (Powered by ADK & Gemini)")
st.markdown("""
Interact with an AI agent that can fetch news from BBC/NPR or just chat.
**Examples:**
*   Ask for `latest news` (gets past 7 days).
*   Request `news from YYYY-MM-DD` (e.g., `news from 2024-04-10`).
*   Use `news from today` or `news from yesterday`.
*   After a briefing, ask follow-up questions like `tell me more about the first item` or `what was the link for the NPR story?` (The agent uses its memory!).
*(Note: News feed history is typically limited to ~2 weeks)*
""")
st.divider() # Add a visual separator
# --- API Key Availability Check ---
# Verify that the GOOGLE_API_KEY is loaded and not the placeholder value.
api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key or "YOUR_GOOGLE_API_KEY" in api_key:
    st.error(
        "🚨 **Action Required: Google API Key Not Found or Invalid!** 🚨\n\n"
        "1. Create a file named `.env` in the same directory as `news_app.py`.\n"
        "2. Add the following line to the `.env` file:\n"
        "   `GOOGLE_API_KEY='YOUR_ACTUAL_GEMINI_API_KEY'`\n"
        "3. Replace `YOUR_ACTUAL_GEMINI_API_KEY` with your valid key from Google AI Studio.\n"
        "4. **Restart the Streamlit application.**",
        icon="🔥"
    )
    st.stop() # Halt further execution if the key is missing or invalid
# --- Initialize ADK Runner and Session ---
# This block attempts to get the initialized ADK components.
# Thanks to @st.cache_resource on initialize_adk(), this runs only once
# per browser session unless the cache is cleared or the script changes significantly.
try:
    adk_runner, current_session_id = initialize_adk()
    # Display initialization success and part of the session ID in the sidebar
    st.sidebar.success(f"ADK Initialized\nSession: ...{current_session_id[-12:]}", icon="✅")
except Exception as e:
    # If ADK initialization fails (e.g., API error, configuration issue), display a critical error.
    st.error(f"**Fatal Error:** Could not initialize the ADK Runner or Session Service: {e}", icon="❌")
    st.error("Please check the terminal logs for more details, ensure your API key is valid, and restart the application.")
    logging.exception("Critical ADK Initialization failed in Streamlit UI context.")
    st.stop() # Stop the app if ADK fails to initialize
# --- Chat Interface Implementation ---
# Use Streamlit's session state to store the chat message history.
# This makes the chat history persist across reruns of the script triggered by UI interactions.
message_history_key = "messages_final_mem_v2" # Use the same key consistently
if message_history_key not in st.session_state:
    # If no history exists for this session, initialize it as an empty list.
    st.session_state[message_history_key] = []
    print("Initialized Streamlit message history.")
# Display the existing chat messages from the history.
# This runs every time the script reruns (e.g., after user input).
# print(f"Displaying {len(st.session_state[message_history_key])} messages from history.")
for message in st.session_state[message_history_key]:
    # Use st.chat_message to render messages with appropriate icons (user/assistant).
    with st.chat_message(message["role"]):
        # Render message content using Markdown. Ensure HTML is not allowed for security.
        st.markdown(message["content"], unsafe_allow_html=False)
# Chat input field at the bottom of the page.
# `st.chat_input` returns the user's text when they press Enter or click Send.
if prompt := st.chat_input("Ask for news (e.g., 'latest news'), follow up, or just chat..."):
    print(f"User input received: '{prompt[:50]}...'")
    # 1. Append and display the user's message immediately.
    st.session_state[message_history_key].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt, unsafe_allow_html=False)
    # 2. Process the user's prompt with the ADK agent and display the response.
    with st.chat_message("assistant"):
        # Use st.empty() as a placeholder to update with the full response later.
        # This gives a slightly better UX than just waiting and then showing the text.
        message_placeholder = st.empty()
        # Show a thinking indicator while the backend processes the request.
        with st.spinner("Assistant is thinking... (Fetching news if needed)"):
            try:
                # Call the synchronous wrapper function to run the ADK agent turn.
                agent_response = run_adk_sync(adk_runner, current_session_id, prompt)
                # Update the placeholder with the agent's complete response.
                message_placeholder.markdown(agent_response, unsafe_allow_html=False)
            except Exception as e:
                # If an error occurs during the ADK run, display it in the chat.
                error_msg = f"Sorry, an error occurred while processing your request: {e}"
                st.error(error_msg) # Show error prominently in the chat UI
                agent_response = f"Error: Failed to get response. {e}" # Store simplified error in history
                logging.exception("Error occurred within the Streamlit chat input processing block.")
    # 3. Append the agent's response (or error message) to the chat history.
    st.session_state[message_history_key].append({"role": "assistant", "content": agent_response})
    # Streamlit automatically reruns the script here, which redraws the chat history including the new messages.
    print("Agent response added to history. Streamlit will rerun.")

# --- Sidebar Information Display ---
# Add useful information to the sidebar for context/debugging.
st.sidebar.divider()
st.sidebar.header("Agent Details")
st.sidebar.caption(f"**Agent Name:** `{APP_NAME_FOR_ADK}`")
st.sidebar.caption(f"**User ID:** `{USER_ID}`")
# Display the active ADK session ID (retrieve safely from st.session_state)
print(f"----------- ADK Session ID: {st.session_state.get('adk_session_id_final_mem_v2', 'N/A')} ---")
# st.sidebar.caption(f"**Session ID:** `{st.session_state.get('streamlit_session_final_mem_v2_1749650406_34dbb9c9', 'N/A')}`")
st.sidebar.caption(f"**LLM Model:** `{MODEL_GEMINI}`")
st.sidebar.caption("Powered by Google Agent Development Kit.")
# Optional: Display raw state for debugging
# with st.sidebar.expander("Show Raw ADK Session State"):
#    try:
#        current_session = adk_runner.session_service.get_session(app_name=APP_NAME_FOR_ADK, user_id=USER_ID, session_id=current_session_id)
#        if current_session:
#            st.json(current_session.state)
#        else:
#            st.write("Session not found in service.")
#    except Exception as e:
#        st.error(f"Could not retrieve session state: {e}")

print("✅ Streamlit UI Rendering Complete.")