import os
import json
import hmac
import io
import re
import secrets
import uuid
import warnings
from datetime import datetime
from flask import Flask, request, render_template, url_for, jsonify, session, redirect, flash, abort, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from sqlalchemy import case, func, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from models import db, User, GameSession
from pvp.models import (
    PvPLobby,
    PvPLobbyKickNotice,
    PvPLobbyMember,
    PvPMatch,
    PvPMatchAttempt,
    PvPMatchPlayer,
    PvPMatchRound,
)
from pvp.routes import pvp_bp
from pvp.scoring import load_balance_config
from pvp.schema import upgrade_pvp_schema
from avatar_utils import avatar_url
from word_dictionary import EN_US_DICTIONARY, get_random_answer, validate_guess
from pvp.models import PvPMatch, PvPMatchResult, PvPPlayerStats
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename
from connections.models import ConnectionsPuzzle, ConnectionsCategory, ConnectionsWord
from connections.routes import register_connections_admin
from cyberpunk_warehouse import register_blueprints as register_cyberpunk_warehouse_blueprints
from cyberpunk_warehouse import seed_builtin_game_modes as seed_cyberpunk_game_modes
from cyberpunk_warehouse.schema import upgrade_cyberpunk_warehouse_schema

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'wordle_secret_key_2024_with_database')
app.config['FLASK_ENV'] = os.getenv('FLASK_ENV', 'development')
app.config['PORTAL_NAME'] = os.getenv('PORTAL_NAME', 'Ігрова кімната')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///wordle_users.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PVP_BALANCE'] = load_balance_config()
app.config['AVATAR_UPLOAD_DIR'] = os.getenv(
    'AVATAR_UPLOAD_DIR', os.path.join(app.instance_path, 'avatars')
)
app.config['AVATAR_MAX_BYTES'] = 5 * 1024 * 1024
app.config['CYBERPUNK_ASSET_MAX_BYTES'] = int(os.getenv('CYBERPUNK_ASSET_MAX_BYTES', str(5 * 1024 * 1024)))
if app.config['CYBERPUNK_ASSET_MAX_BYTES'] < 1:
    raise RuntimeError('CYBERPUNK_ASSET_MAX_BYTES must be a positive integer.')
app.config['MAX_CONTENT_LENGTH'] = max(
    app.config['AVATAR_MAX_BYTES'], app.config['CYBERPUNK_ASSET_MAX_BYTES']
) + 128 * 1024
app.config['CYBERPUNK_ASSET_DIR'] = os.getenv(
    'CYBERPUNK_ASSET_DIR', os.path.join(app.instance_path, 'cyberpunk_warehouse', 'assets')
)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
oauth = OAuth(app)
app.register_blueprint(pvp_bp)
register_connections_admin(app)
register_cyberpunk_warehouse_blueprints(app)


app.jinja_env.globals['avatar_url'] = avatar_url


def _avatar_csrf_token():
    token = session.get('avatar_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['avatar_csrf_token'] = token
    return token


def _check_avatar_csrf():
    expected = session.get('avatar_csrf_token', '')
    supplied = request.form.get('csrf_token', '')
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description='Invalid form security token.')


_AVATAR_FILENAME_RE = re.compile(r'^[0-9a-f]{32}\.webp$')
_AVATAR_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
_AVATAR_MIMES = {
    'JPEG': {'image/jpeg', 'image/jpg'},
    'PNG': {'image/png'},
    'WEBP': {'image/webp'},
}


def _process_avatar_upload(upload):
    """Validate and normalize an uploaded image into a metadata-free square WebP."""
    original_name = secure_filename(upload.filename or '')
    if not original_name or os.path.splitext(original_name)[1].lower() not in _AVATAR_EXTENSIONS:
        raise ValueError('Unsupported file format.')
    supplied_mime = (upload.mimetype or '').lower()
    if supplied_mime and supplied_mime not in {
        'application/octet-stream', 'image/jpeg', 'image/jpg', 'image/png', 'image/webp'
    }:
        raise ValueError('Unsupported file format.')
    raw = upload.stream.read(app.config['AVATAR_MAX_BYTES'] + 1)
    if len(raw) > app.config['AVATAR_MAX_BYTES']:
        raise OverflowError('File is too large.')
    if not raw:
        raise ValueError('Unable to process image.')

    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                image_format = probe.format
                width, height = probe.size
                if image_format not in _AVATAR_MIMES:
                    raise ValueError('Unsupported file format.')
                if supplied_mime and supplied_mime not in ('application/octet-stream',) and supplied_mime not in _AVATAR_MIMES[image_format]:
                    raise ValueError('Unsupported file format.')
                expected_mime = {'JPEG': {'.jpg', '.jpeg'}, 'PNG': {'.png'}, 'WEBP': {'.webp'}}[image_format]
                if os.path.splitext(original_name)[1].lower() not in expected_mime:
                    raise ValueError('Unsupported file format.')
                if width < 1 or height < 1 or width > 8192 or height > 8192 or width * height > 16_000_000:
                    raise ValueError('Unable to process image.')
                probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            oriented = ImageOps.exif_transpose(source)
            if oriented.mode not in ('RGB', 'RGBA'):
                oriented = oriented.convert('RGBA' if 'transparency' in oriented.info else 'RGB')
            square = ImageOps.fit(oriented, (512, 512), method=Image.Resampling.LANCZOS)
            output = io.BytesIO()
            square.save(output, format='WEBP', quality=90, method=6, exif=b'')
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('Unable to process image.') from exc


@app.route('/avatars/<filename>', methods=['GET'])
def serve_avatar(filename):
    if not _AVATAR_FILENAME_RE.fullmatch(filename or ''):
        abort(404)
    if not User.query.filter_by(custom_avatar=filename).first():
        abort(404)
    directory = app.config['AVATAR_UPLOAD_DIR']
    response = send_from_directory(directory, filename, mimetype='image/webp', max_age=31536000)
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.route('/profile/avatar', methods=['POST'])
@login_required
def upload_avatar():
    _check_avatar_csrf()
    upload = request.files.get('avatar')
    if not upload or not upload.filename:
        flash('Choose an image to upload.', 'error')
        return redirect(url_for('profile'))
    try:
        webp_data = _process_avatar_upload(upload)
    except OverflowError:
        flash('File is too large. Maximum size is 5 MB.', 'error')
        return redirect(url_for('profile'))
    except ValueError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('profile'))

    directory = app.config['AVATAR_UPLOAD_DIR']
    filename = f'{secrets.token_hex(16)}.webp'
    path = os.path.join(directory, filename)
    old_filename = current_user.custom_avatar
    try:
        os.makedirs(directory, mode=0o750, exist_ok=True)
        with open(path, 'xb') as target:
            target.write(webp_data)
        current_user.custom_avatar = filename
        db.session.commit()
    except Exception:
        db.session.rollback()
        try:
            os.remove(path)
        except OSError:
            pass
        app.logger.exception('Avatar upload could not be committed')
        flash('Upload failed. Please try again.', 'error')
        return redirect(url_for('profile'))

    if old_filename and old_filename != filename and _AVATAR_FILENAME_RE.fullmatch(old_filename):
        if not User.query.filter_by(custom_avatar=old_filename).first():
            try:
                os.remove(os.path.join(directory, old_filename))
            except OSError:
                app.logger.warning('Could not remove replaced avatar file')
    flash('Avatar updated successfully.', 'success')
    return redirect(url_for('profile'))


@app.route('/profile/avatar/delete', methods=['POST'])
@login_required
def delete_avatar():
    _check_avatar_csrf()
    old_filename = current_user.custom_avatar
    if old_filename:
        current_user.custom_avatar = None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('Avatar removal could not be committed')
            flash('Unable to remove avatar. Please try again.', 'error')
            return redirect(url_for('profile'))
        if _AVATAR_FILENAME_RE.fullmatch(old_filename) and not User.query.filter_by(custom_avatar=old_filename).first():
            try:
                os.remove(os.path.join(app.config['AVATAR_UPLOAD_DIR'], old_filename))
            except FileNotFoundError:
                pass
            except OSError:
                app.logger.warning('Could not remove deleted avatar file')
    flash('Avatar removed.', 'success')
    return redirect(url_for('profile'))


@app.errorhandler(413)
def handle_request_too_large(error):
    if request.endpoint == 'upload_avatar':
        flash('File is too large. Maximum size is 5 MB.', 'error')
        return redirect(url_for('profile'))
    if request.path.startswith('/admin/cyberpunk-warehouse/assets'):
        max_mb = app.config['CYBERPUNK_ASSET_MAX_BYTES'] / (1024 * 1024)
        flash(f'Image is too large. Maximum size is {max_mb:g} MB.', 'error')
        return redirect(url_for('cyberpunk_warehouse_admin.assets'))
    return error

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

def pick_word(previous_word=None):
    """Pick a local en-US answer, avoiding the current session's last word."""
    return get_random_answer(
        language="en-US",
        word_length=5,
        exclude=(previous_word or "",),
    ).upper()


def _ensure_classic_game_id():
    game_id = session.get('game_id')
    if not isinstance(game_id, str) or not re.fullmatch(r'[0-9a-f]{32}', game_id):
        game_id = uuid.uuid4().hex
        session['game_id'] = game_id
    return game_id


def _classic_user_stats():
    return {
        'games_played': current_user.games_played or 0,
        'games_won': current_user.games_won or 0,
        'win_percentage': current_user.win_percentage,
        'current_streak': current_user.current_streak or 0,
        'max_streak': current_user.max_streak or 0,
    }


def _record_classic_result(game_id, target_word, guesses, won, tries):
    """Record one terminal or abandoned game and update account totals once."""
    result_insert = sqlite_insert(GameSession).values(
        game_id=game_id,
        user_id=current_user.id,
        target_word=target_word,
        guesses=json.dumps(guesses),
        won=bool(won),
        tries_used=tries,
        completed_at=datetime.utcnow(),
    ).on_conflict_do_nothing(index_elements=[GameSession.game_id])
    inserted = db.session.execute(result_insert).rowcount == 1
    if inserted:
        next_streak = func.coalesce(User.current_streak, 0) + 1
        next_best = case(
            (next_streak > func.coalesce(User.max_streak, 0), next_streak),
            else_=func.coalesce(User.max_streak, 0),
        )
        db.session.execute(
            update(User).where(User.id == current_user.id).values(
                games_played=func.coalesce(User.games_played, 0) + 1,
                games_won=func.coalesce(User.games_won, 0) + (1 if won else 0),
                current_streak=case(
                    (bool(won), next_streak),
                    else_=0,
                ),
                max_streak=case(
                    (bool(won), next_best),
                    else_=func.coalesce(User.max_streak, 0),
                ),
            )
        )
    db.session.commit()
    db.session.refresh(current_user)
    return inserted

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
        session['target_word'] = pick_word(session.get('target_word'))
        session['tries'] = 0
        session['guesses'] = []
        session['game_over'] = False
        session['won'] = False
        session['game_id'] = uuid.uuid4().hex
    _ensure_classic_game_id()
    
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
    """Show the signed-in user's gaming dashboard."""
    return _render_player_profile(current_user, is_owner=True)


@app.route('/profile/<int:user_id>')
def public_profile(user_id):
    """Public, privacy-safe PvP profile for a player."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    is_owner = current_user.is_authenticated and current_user.id == user.id
    if is_owner:
        return _render_player_profile(user, is_owner=True)
    return _render_player_profile(user, is_owner=False)


def _render_player_profile(user, *, is_owner):
    stats = db.session.get(PvPPlayerStats, user.id)
    rank_query = db.session.query(
        PvPPlayerStats.user_id.label('user_id'),
        func.row_number().over(order_by=(
            PvPPlayerStats.total_score.desc(),
            PvPPlayerStats.wins.desc(),
            PvPPlayerStats.best_match_score.desc(),
            PvPPlayerStats.user_id.asc(),
        )).label('rank'),
    ).filter(PvPPlayerStats.matches_played > 0).subquery()
    rank_row = db.session.query(rank_query.c.rank).filter(
        rank_query.c.user_id == user.id
    ).first()
    rank = rank_row.rank if rank_row else None

    recent_matches = []
    average_attempts = None
    if is_owner and stats is not None:
        recent_matches = PvPMatchResult.query.options(
            joinedload(PvPMatchResult.match).selectinload(PvPMatch.players),
            joinedload(PvPMatchResult.match).selectinload(PvPMatch.results),
        ).join(PvPMatch).filter(
            PvPMatchResult.user_id == user.id,
            PvPMatch.status == 'completed',
        ).order_by(
            func.coalesce(PvPMatch.completed_at, PvPMatchResult.finalized_at).desc(),
            PvPMatch.id.desc(),
        ).limit(5).all()
        completed_results = PvPMatchResult.query.filter_by(
            user_id=user.id, participation_status='completed'
        ).all()
        solved_words = sum(result.words_solved for result in completed_results)
        if solved_words:
            average_attempts = round(
                sum(result.total_attempts for result in completed_results) / solved_words, 2
            )

    matches_played = stats.matches_played if stats else 0
    wins = stats.wins if stats else 0
    profile_stats = {
        'matches_played': matches_played,
        'wins': wins,
        'win_rate': round(wins * 100 / matches_played, 1) if matches_played else 0,
        'total_score': stats.total_score if stats else 0,
        'best_match_score': stats.best_match_score if stats else 0,
        'average_attempts': average_attempts,
    }
    return render_template(
        'profile.html', user=user, stats=profile_stats, rank=rank,
        recent_matches=recent_matches, is_owner=is_owner,
        avatar_csrf_token=_avatar_csrf_token() if is_owner else None,
    )

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

    guess, validation_error = validate_guess(raw_guess, word_length=5, language="en-US")
    if validation_error:
        return jsonify({'error': validation_error}), 400

    target_word = session.get('target_word')
    _ensure_classic_game_id()
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
            _record_classic_result(
                _ensure_classic_game_id(), target_word, guesses, won, tries
            )
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
        'user_stats': _classic_user_stats() if game_over else None
    }
    
    return jsonify(response_data)

@app.route('/new_game', methods=['POST'])
@login_required
def new_game():
    """Record an in-progress abandoned loss, then start a fresh game."""
    guesses = session.get('guesses', [])
    if not isinstance(guesses, list):
        guesses = []
    attempts = len(guesses)
    abandoned_loss = False
    game_over = bool(session.get('game_over', False))
    target_word = session.get('target_word')

    # A zero-attempt game is free to restart. Terminal results were already
    # committed by /guess and must never be counted again here.
    if attempts and not game_over and EN_US_DICTIONARY.contains(target_word):
        try:
            _record_classic_result(
                _ensure_classic_game_id(), target_word, guesses, False, attempts
            )
        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception('Could not persist abandoned game for user %s', current_user.id)
            return jsonify({
                'error': 'Unable to record this game result. Please try again; your current game is unchanged.'
            }), 503
        abandoned_loss = True

    session['target_word'] = pick_word(session.get('target_word'))
    session['tries'] = 0
    session['guesses'] = []
    session['game_over'] = False
    session['won'] = False
    session['game_id'] = uuid.uuid4().hex
    return jsonify({
        'message': 'New game started',
        'abandoned_loss': abandoned_loss,
        'user_stats': _classic_user_stats() if abandoned_loss else None,
    })

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
    """Create missing tables, apply additive upgrades, and seed new built-in modes."""
    with app.app_context():
        db.create_all()
        upgrade_pvp_schema()
        upgrade_cyberpunk_warehouse_schema(db.engine)
        seed_cyberpunk_game_modes()


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
