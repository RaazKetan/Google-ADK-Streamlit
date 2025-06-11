# 📰 ADK News & Chat Agent

A smart news assistant built with Streamlit, Google's Agent Development Kit (ADK), and Gemini AI that fetches real-time news from BBC and NPR, answers follow-up questions, and provides general chat functionality.

## 🎯 What This App Does

- **Fetches Latest News**: Gets current headlines from BBC and NPR RSS feeds
- **Smart Date Filtering**: Ask for news from specific dates or time periods
- **Memory & Follow-ups**: Remembers your last news briefing and can answer questions about specific stories
- **General Chat**: Works as a regular AI assistant for any topic
- **Caching**: Efficiently caches news data to avoid unnecessary requests

## 🚀 Features

### News Functionality
- Request latest news (past 7 days by default)
- Get news from specific dates: `news from today`, `news from yesterday`, `news from 2024-12-10`
- Follow-up questions: "Tell me more about the first story", "What's the link for that BBC article?"

### Technical Features
- Built with Google Agent Development Kit (ADK) for advanced AI agent capabilities
- Uses Gemini 2.0 Flash model for natural language processing
- RSS feed caching with ETag/Last-Modified headers for efficiency
- Session state management for conversation memory
- Clean, responsive Streamlit interface

## 📋 Requirements

### Software Requirements
- **Python 3.8 or higher**
- **Google API Key** (for Gemini AI)
- Internet connection (for fetching news feeds)

### Python Packages
All required packages are listed in `requirements.txt`:
- `streamlit` - Web app framework
- `feedparser` - RSS feed parsing
- `python-dotenv` - Environment variable management
- `google-adk` - Google Agent Development Kit

## 🛠️ Installation & Setup

### Step 1: Clone or Download the Project
```bash
git clone <your-repository-url>
cd ADK_NEWS_AGENT
```

### Step 2: Create a Virtual Environment (Recommended)
```bash
# Create virtual environment
python -m venv .venv

# Activate it (macOS/Linux)
source .venv/bin/activate

# Activate it (Windows)
.venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Get Your Google API Key
1. Go to [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy the generated API key

### Step 5: Create Environment File
Create a file named `.env` in the project root directory and add:
```
GOOGLE_GENAI_USE_VERTEXAI="FALSE"
GOOGLE_API_KEY=your_actual_api_key_here
```
**Important**: Replace `your_actual_api_key_here` with your real API key from Step 4.

### Step 6: Run the Application
```bash
streamlit run news_app.py
```

The app will open in your web browser at `http://localhost:8501`

## 🎮 How to Use

### Basic News Requests
- **Latest news**: Type `latest news` or `what's happening today`
- **Today's news**: Type `news from today`
- **Yesterday's news**: Type `news from yesterday`
- **Specific date**: Type `news from 2024-12-10` (YYYY-MM-DD format)

### Follow-up Questions
After getting a news briefing, you can ask:
- `Tell me more about the first story`
- `What was the link for that BBC article?`
- `Expand on the NPR story about...`
- `Give me more details about item 3`

### General Chat
- Ask any general questions
- Have normal conversations
- Get help with various topics

## 📁 Project Structure

```
ADK_NEWS_AGENT/
├── news_app.py          # Main application file
├── requirements.txt     # Python dependencies
├── .env                # Environment variables (you create this)
├── .gitignore          # Git ignore file
├── README.md           # This file
└── check.py            # Simple RSS feed testing script (optional)
```

## 🔧 Configuration

### Environment Variables
- `GOOGLE_API_KEY`: Your Google Gemini API key (required)
- `GOOGLE_GENAI_USE_VERTEXAI`: Set to "FALSE" to use standard Gemini API

### Application Settings (in news_app.py)
- `MAX_ITEMS_TO_PROCESS`: Maximum news items to process (default: 200)
- `MODEL_GEMINI`: AI model to use (default: "gemini-2.0-flash")
- News sources: BBC World News and NPR Headlines (can be modified)

## 🐛 Troubleshooting

### Common Issues

#### 1. "API Key Not Found" Error
**Problem**: The app shows a red error about missing API key.
**Solution**: 
- Make sure your `.env` file exists in the project root
- Check that your API key is correctly copied (no extra spaces)
- Restart the Streamlit app after creating the `.env` file

#### 2. "Session Not Found" Error
**Problem**: Error message about missing session.
**Solution**: 
- This happens when the app restarts but keeps old session data
- Simply refresh your browser page
- The app automatically handles this in most cases

#### 3. No News Items Found
**Problem**: App says no news found for your date request.
**Solution**:
- Try a more recent date (RSS feeds typically have 1-2 weeks of history)
- Check your internet connection
- Try `latest news` instead of a specific date

#### 4. Slow Response Times
**Problem**: App takes a long time to respond.
**Solution**:
- First request is slower as it fetches and caches news
- Subsequent requests use cached data and are faster
- Check your internet connection speed

### Getting Help
If you encounter issues:
1. Check the terminal/console for detailed error messages
2. Make sure all requirements are installed correctly
3. Verify your Python version is 3.8 or higher
4. Ensure your Google API key is valid and has proper permissions

## 🔒 Security & Privacy

- **API Key Security**: Never share your `.env` file or commit it to version control
- **Data Storage**: News data is cached in memory only (lost when app restarts)
- **No Personal Data**: The app doesn't store personal information
- **RSS Feeds**: Only fetches publicly available news feeds

## 🚀 Advanced Usage

### Customizing News Sources
To add or change news sources, modify the `DEFAULT_FEED_URLS` list in `news_app.py`:
```python
DEFAULT_FEED_URLS = [
    "https://feeds.bbci.co.uk/news/rss.xml",  # BBC
    "https://feeds.npr.org/1001/rss.xml",     # NPR
    "your_additional_rss_feed_url_here"       # Add more
]
```

### Understanding the Code Structure
- **Tool Function**: `fetch_and_return_news()` handles RSS fetching and filtering
- **Agent Definition**: `root_agent` defines the AI's behavior and instructions
- **Session Management**: ADK handles conversation state and memory
- **UI Components**: Streamlit provides the web interface

## 📝 License

MIT License - feel free to use and modify as needed.

## 🤝 Contributing

This is a learning project! Feel free to:
- Report bugs or issues
- Suggest new features
- Submit improvements
- Ask questions about the code

## 📚 Learn More

- [Streamlit Documentation](https://docs.streamlit.io/)
- [Google AI Studio](https://makersuite.google.com/)
- [Feedparser Documentation](https://feedparser.readthedocs.io/)
- [Python Virtual Environments](https://docs.python.org/3/tutorial/venv.html)

---

**Happy news reading! 📰✨**# Google-ADK-Streamlit
# Google-ADK-Streamlit
