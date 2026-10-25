# 🎮 Wordle Web Game with User Authentication

A web-based implementation of the popular Wordle game built with Flask, featuring Google OAuth authentication, user profiles, statistics tracking, and an interactive on-screen keyboard with color-coded feedback.

🌐 **Live Demo**: [Coming soon - deploying to Fly.io]

## 🚀 Quick Start

### Local Development
```bash
git clone https://github.com/yourusername/wordle-game.git
cd wordle-game
pip install -r requirements.txt
python app_with_auth.py
```

### Deploy to Fly.io
```bash
fly launch
fly deploy
```

## 🎮 Features

### Game Features
- **Interactive Gameplay**: Full web-based Wordle experience
- **On-Screen Keyboard**: QWERTY layout with mouse/touch input
- **Color-Coded Feedback**: 
  - 🟢 Green: Correct letter in correct position
  - 🟡 Yellow: Correct letter in wrong position
  - ⚫ Gray: Letter not in the word
  - ⚪ White: Letter not yet used
- **Mobile Responsive**: Optimized for mobile devices and tablets

### User Management Features
- **Google OAuth Login**: Secure authentication with Google accounts
- **User Profiles**: Personal statistics and game history
- **Progress Tracking**: Games played, win rate, streaks
- **Leaderboard**: Compete with other players
- **Persistent Sessions**: Your progress is saved across sessions

### Technical Features
- **Multi-User Support**: Multiple players can play simultaneously
- **Database Storage**: SQLite database for user data and game sessions
- **Internet Access**: Public access via ngrok tunneling
- **Production Ready**: Waitress WSGI server for deployment

## 🚀 Quick Start

### Prerequisites
1. Python 3.7+
2. Google Cloud account (for OAuth setup)
3. ngrok (for internet access)

### Setup Instructions

1. **Install Dependencies**:
   ```bash
   python -m pip install -r requirements.txt
   ```

2. **Set up Google OAuth** (Required for authentication):
   - Follow the detailed guide in `GOOGLE_OAUTH_SETUP.md`
   - Update your `.env` file with Google OAuth credentials

3. **Start the Application**:
   
   **Option A: With Authentication (Recommended)**
   ```bash
   python app_with_auth.py
   ```
   
   **Option B: Basic Version (No Authentication)**
   ```bash
   python app_production.py
   ```

4. **For Internet Access**:
   - Use `start_server_multi_user.bat` to automatically start server + ngrok
   - Share the ngrok URL with friends

## 📁 Application Versions

- **`app_with_auth.py`** - Full-featured app with Google OAuth, user profiles, and statistics
- **`app_production.py`** - Production-ready app without authentication
- **`app.py`** - Basic development version

## 🔧 Configuration

### Environment Variables (.env file)
```env
# Google OAuth Configuration
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Flask Configuration
FLASK_SECRET_KEY=your_secret_key_here
DATABASE_URL=sqlite:///wordle.db
```

### Database Setup
The application automatically creates the SQLite database on first run. No manual setup required.

## 🎯 Game Rules

1. Guess the 5-letter word in 6 tries
2. Each guess must be a valid 5-letter word
3. After each guess, letters are colored to show how close you are:
   - Green: Right letter, right position
   - Yellow: Right letter, wrong position
   - Gray: Letter not in the word

## 📚 Guess Dictionary

The server validates guesses against the packaged American English dictionary at
`dictionaries/en-US/words.txt`. The same complete list supplies hidden answers.
It contains 8,754 normalized five-letter words and is loaded once when the
application starts. To rebuild it from the pinned upstream source, run:

```bash
python build_dictionary.py
```

The source release, license notice, retrieval date, and filtering details are in
[`dictionaries/en-US/README.md`](dictionaries/en-US/README.md). The dictionary is
included in the application image and does not use the SQLite database or Fly
Volume.

## 👤 User Features

### Profile Page
- View personal statistics
- See recent game history
- Track win rate and streaks

### Leaderboard
- Compare your performance with other players
- Rankings based on win rate and streaks
- Real-time updates

### Statistics Tracked
- Total games played
- Games won
- Win percentage
- Current win streak
- Best win streak
- Individual game results

## 🌐 Deployment Options

### Local Development
```bash
python app_with_auth.py
# Access at http://localhost:8000
```

### Public Access (ngrok)
```bash
# Start server and ngrok tunnel
start_server_multi_user.bat

# Or manually:
python app_with_auth.py &
ngrok http 8000
```

### Production Deployment
- Use a proper web server (nginx + gunicorn/waitress)
- Set up SSL/HTTPS
- Use environment variables for configuration
- Consider PostgreSQL/MySQL for the database

## 📱 Mobile Support

The application is fully responsive and optimized for:
- Smartphones (iOS/Android)
- Tablets
- Desktop browsers
- Touch and mouse input

## 🛠 Technical Stack

- **Backend**: Flask, SQLAlchemy, Flask-Login
- **Authentication**: Google OAuth 2.0 (Authlib)
- **Database**: SQLite (development), configurable for production
- **Frontend**: HTML5, CSS3, JavaScript (ES6)
- **Deployment**: Waitress WSGI server
- **Tunneling**: ngrok for public access

## 📄 File Structure

```
Wordle_API/
├── app_with_auth.py          # Main application with authentication
├── app_production.py         # Production app without auth
├── app.py                    # Basic development version
├── models.py                 # Database models
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (create this)
├── words.json               # Game word list
├── templates/
│   ├── index.html           # Main game interface
│   ├── login.html           # Login page
│   ├── profile.html         # User profile page
│   └── leaderboard.html     # Leaderboard page
├── static/
│   ├── style.css            # Game styling
│   └── script.js            # Game logic and interactions
├── start_server_multi_user.bat  # Easy startup script
├── stop_server.bat          # Stop all processes
├── README.md                # This file
└── GOOGLE_OAUTH_SETUP.md    # OAuth setup guide
```

## 🔒 Security Notes

- Google OAuth provides secure authentication
- User sessions are encrypted
- Environment variables protect sensitive data
- Database uses parameterized queries to prevent SQL injection

## 🐛 Troubleshooting

### Common Issues

1. **OAuth Errors**: Check `GOOGLE_OAUTH_SETUP.md` for detailed setup instructions
2. **Database Issues**: Delete `wordle.db` to reset the database
3. **Port Conflicts**: Change the port in the application files if 8000 is in use
4. **ngrok Issues**: Restart ngrok tunnel if the URL becomes invalid

### Getting Help

- Check the console output for error messages
- Review the Google OAuth setup guide
- Ensure all dependencies are installed correctly

## 🤝 Contributing

Feel free to submit issues and enhancement requests!

## 📜 License

This project is for educational and entertainment purposes.

---

**Enjoy playing Wordle with friends! 🎉**
