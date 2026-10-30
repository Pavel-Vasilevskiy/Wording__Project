import os
import json
from datetime import datetime
from flask import Flask, request, render_template, url_for, jsonify, session, redirect, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from sqlalchemy.exc import SQLAlchemyError
from models import db, User, GameSession
from pvp.models import (
    PvPLobby,
    PvPLobbyKickNotice,
    PvPLobbyMember,
    PvPMatch,
    PvPMatchPlayer,
    PvPMatchRound,
)
from pvp.routes import pvp_bp
from pvp.schema import upgrade_pvp_schema
from word_dictionary import EN_US_DICTIONARY, validate_guess

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'wordle_secret_key_2024_with_database')
app.config['PORTAL_NAME'] = os.getenv('PORTAL_NAME', 'Ігрова кімната')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///wordle_users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
oauth = OAuth(app)
app.register_blueprint(pvp_bp)

# Configure Google OAuth
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    access_token_url='https://oauth2.googleapis.com/token',
    access_token_params=None,
    refresh_token_url=None,
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',
    client_kwargs={'scope': 'openid email profile'},
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def pick_word():
    """Pick a uniformly random answer from the loaded en-US dictionary."""
    return EN_US_DICTIONARY.choose_answer().upper()

def check_guess(guess, target_word):
    """Check the guess against target word and return color coding"""
    result = []
    guess = guess.upper()
    target_word = target_word.upper()
    
    for i, char in enumerate(guess):
        if char == target_word[i]:
            result.append({'letter': char, 'status': 'green'})
        elif char in target_word:
            result.append({'letter': char, 'status': 'yellow'})
        else:
            result.append({'letter': char, 'status': 'gray'})
    
    return result

@app.route('/')
def index():
    """Show the public game catalog."""
    return render_template('home.html', portal_name=app.config['PORTAL_NAME'])


@app.route('/wordle')
@login_required
def wordle():
    """Main Wordle game page."""
    # Initialize new game if not exists
    if not EN_US_DICTIONARY.contains(session.get('target_word')):
        session['target_word'] = pick_word()
        session['tries'] = 0
        session['guesses'] = []
        session['game_over'] = False
        session['won'] = False
    
    return render_template("index.html", user=current_user, portal_name=app.config['PORTAL_NAME'])

@app.route('/game_state')
@login_required
def game_state():
    """Return the current session's saved game state for page restoration."""
    guesses = session.get('guesses', [])
    if not isinstance(guesses, list):
        guesses = []

    # Guess history is the source of truth for the current row and attempt count.
    tries = len(guesses)
    game_over = bool(session.get('game_over', False))
    won = bool(session.get('won', False)) if game_over else False
    return jsonify({
        'guesses': guesses,
        'tries': tries,
        'game_over': game_over,
        'won': won,
        # Do not disclose the answer until the game has ended.
        'target_word': session.get('target_word') if game_over else None,
    })

@app.route('/login')
def login():
    """Login page"""
    if current_user.is_authenticated:
        return redirect(url_for('wordle'))
    return render_template('login.html')

@app.route('/auth/google')
def google_auth():
    """Initiate Google OAuth"""
    # Use dynamic redirect URI based on the current request
    redirect_uri = url_for('google_callback', _external=True)
    print(f"🔍 Debug: Using redirect URI: {redirect_uri}")
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def google_callback():
    """Handle Google OAuth callback"""
    try:
        print("🔍 Debug: Starting OAuth callback")
        token = google.authorize_access_token()
        print(f"🔍 Debug: Token received: {bool(token)}")
        
        # Get user info from Google
        resp = google.get('https://www.googleapis.com/oauth2/v2/userinfo', token=token)
        user_info = resp.json()
        print(f"🔍 Debug: User info received: {user_info}")
        
        if user_info and 'id' in user_info:
            # Check if user exists (Google v2 API uses 'id' not 'sub')
            google_id = user_info['id']
            print(f"🔍 Debug: Google ID: {google_id}")
            
            user = User.query.filter_by(google_id=google_id).first()
            print(f"🔍 Debug: Existing user found: {bool(user)}")
            
            if not user:
                # Create new user
                user = User(
                    google_id=google_id,
                    email=user_info.get('email'),
                    name=user_info.get('name'),
                    picture=user_info.get('picture')
                )
                db.session.add(user)
                db.session.commit()
                print(f"✅ Debug: Created new user: {user.name}")
                flash(f'Welcome to Wordle, {user.name}!', 'success')
            else:
                # Update user info
                user.name = user_info.get('name')
                user.picture = user_info.get('picture')
                db.session.commit()
                print(f"✅ Debug: Updated existing user: {user.name}")
                flash(f'Welcome back, {user.name}!', 'success')
            
            login_user(user)
            print(f"✅ Debug: User logged in successfully, redirecting to Wordle")
            return redirect(url_for('wordle'))
        else:
            print(f"❌ Debug: Invalid user info: {user_info}")
            flash('Failed to get user information from Google.', 'error')
            return redirect(url_for('login'))
        
    except Exception as e:
        print(f"❌ Debug: Exception in callback: {str(e)}")
        print(f"❌ Debug: Exception type: {type(e)}")
        import traceback
        print(f"❌ Debug: Traceback: {traceback.format_exc()}")
        flash('Authentication failed. Please try again.', 'error')
    
    print("❌ Debug: Falling back to login redirect")
    return redirect(url_for('login'))

@app.route('/logout')
@login_required
def logout():
    """Logout user"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/profile')
@login_required
def profile():
    """User profile page with statistics"""
    recent_games = GameSession.query.filter_by(user_id=current_user.id)\
                                   .order_by(GameSession.created_at.desc())\
                                   .limit(10).all()
    return render_template('profile.html', user=current_user, recent_games=recent_games)

@app.route('/guess', methods=['POST'])
@login_required
def make_guess():
    """Process a guess"""
    data = request.get_json()
    raw_guess = data.get('guess', '') if isinstance(data, dict) else ''
    if not isinstance(raw_guess, str) or len(raw_guess) != 5:
        return jsonify({'error': 'Word must be 5 letters long'}), 400

    if session.get('game_over', False):
        return jsonify({'error': 'Game is already over. Start a new game.'}), 409

    guess, validation_error = validate_guess(raw_guess, EN_US_DICTIONARY)
    if validation_error:
        return jsonify({'error': validation_error}), 400

    target_word = session.get('target_word')
    guesses = list(session.get('guesses', []))
    # The saved guess history is authoritative; this prevents its length and
    # the current row counter from drifting apart.
    tries = len(guesses)
    
    # Check the guess
    result = check_guess(guess, target_word)
    tries += 1
    guesses.append(result)
    
    # Check if won
    won = guess == target_word
    game_over = won or tries >= 6
    
    # If game is over, save to database and update user stats
    if game_over:
        try:
            game_session = GameSession(
                user_id=current_user.id,
                target_word=target_word,
                guesses=json.dumps(guesses),
                won=won,
                tries_used=tries,
                completed_at=datetime.utcnow()
            )
            db.session.add(game_session)

            # Update account statistics in the same transaction as the result.
            current_user.update_stats(won, tries)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception('Could not persist completed game for user %s', current_user.id)
            return jsonify({'error': 'Unable to save game result. Please try again.'}), 503

    # Only persist the game state in the signed session after its database write succeeds.
    session['tries'] = tries
    session['guesses'] = guesses
    session['game_over'] = game_over
    session['won'] = won
    
    response_data = {
        'result': result,
        'tries': tries,
        'game_over': game_over,
        'won': won,
        'target_word': target_word if game_over else None,
        'user_stats': {
            'games_played': current_user.games_played,
            'games_won': current_user.games_won,
            'win_percentage': current_user.win_percentage,
            'current_streak': current_user.current_streak,
            'max_streak': current_user.max_streak
        } if game_over else None
    }
    
    return jsonify(response_data)

@app.route('/new_game', methods=['POST'])
@login_required
def new_game():
    """Start a new game"""
    session['target_word'] = pick_word()
    session['tries'] = 0
    session['guesses'] = []
    session['game_over'] = False
    session['won'] = False
    return jsonify({'message': 'New game started'})

@app.route('/leaderboard')
@login_required
def leaderboard():
    """Show leaderboard with top players"""
    try:
        # Get all users with games played and sort them by win percentage and max streak
        users_with_games = User.query.filter(User.games_played > 0).all()

        # Sort by win percentage (descending), then by max streak (descending), then by games won (descending)
        top_players = sorted(users_with_games,
                            key=lambda u: (u.win_percentage, u.max_streak, u.games_won),
                            reverse=True)[:20]
    except SQLAlchemyError:
        db.session.rollback()
        app.logger.exception('Could not load leaderboard')
        return render_template('leaderboard.html', top_players=[], load_error=True), 503
    
    return render_template('leaderboard.html', top_players=top_players)


def initialize_database():
    """Create missing tables and apply additive PvP upgrades without dropping data."""
    with app.app_context():
        db.create_all()
        upgrade_pvp_schema()


if __name__ == '__main__':
    initialize_database()
    
    # Check if Google OAuth is configured
    if not os.getenv('GOOGLE_CLIENT_ID') or not os.getenv('GOOGLE_CLIENT_SECRET'):
        print("⚠️  Warning: Google OAuth not configured!")
        print("📝 Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env file")
        print("🔗 Get credentials at: https://console.cloud.google.com/")
    
    # Get port from environment variable (for Fly.io) or default to 8080
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    
    if debug:
        print("🚀 Starting Wordle server with authentication and database")
        print(f"📍 Local access: http://127.0.0.1:{port}")
        print(f"🌐 Network access: http://0.0.0.0:{port}")
        print("👥 Multi-user support with Google login")
        print("💾 User progress saved to database")
        print("🛑 Press Ctrl+C to stop")
    else:
        print(f"🚀 Starting Wordle server in production mode on port {port}")
    
    # Use production-ready server in production mode
    if not debug:
        try:
            from waitress import serve
            print("🌐 Using Waitress production server")
            serve(app, host='0.0.0.0', port=port, threads=10)
        except ImportError:
            print("⚠️  Waitress not installed, falling back to Flask dev server")
            app.run(debug=False, host='0.0.0.0', port=port, threaded=True)
    else:
        app.run(debug=debug, host='0.0.0.0', port=port, threaded=True)
