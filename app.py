from flask import Flask, request, render_template, url_for, jsonify, session
import os
from word_dictionary import EN_US_DICTIONARY, validate_guess

app = Flask(__name__)
app.secret_key = 'wordle_secret_key_2024'  # Needed for sessions

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
    """Main game page"""
    # Initialize new game
    session['target_word'] = pick_word()
    session['tries'] = 0
    session['guesses'] = []
    session['game_over'] = False
    session['won'] = False
    return render_template("index.html")

@app.route('/guess', methods=['POST'])
def make_guess():
    """Process a guess"""
    data = request.get_json()
    raw_guess = data.get('guess', '') if isinstance(data, dict) else ''
    guess, validation_error = validate_guess(raw_guess, EN_US_DICTIONARY)
    if validation_error:
        return jsonify({'error': validation_error}), 400
    
    target_word = session.get('target_word')
    tries = session.get('tries', 0)
    guesses = session.get('guesses', [])
    
    # Check the guess
    result = check_guess(guess, target_word)
    tries += 1
    guesses.append(result)
    
    # Check if won
    won = guess == target_word
    game_over = won or tries >= 6
    
    # Update session
    session['tries'] = tries
    session['guesses'] = guesses
    session['game_over'] = game_over
    session['won'] = won
    
    response_data = {
        'result': result,
        'tries': tries,
        'game_over': game_over,
        'won': won,
        'target_word': target_word if game_over else None
    }
    
    return jsonify(response_data)

@app.route('/new_game', methods=['POST'])
def new_game():
    """Start a new game"""
    session['target_word'] = pick_word()
    session['tries'] = 0
    session['guesses'] = []
    session['game_over'] = False
    session['won'] = False
    return jsonify({'message': 'New game started'})

@app.route('/show_word')
def show_word():
    """Show the current target word (for debugging)"""
    return jsonify({'word': session.get('target_word', 'No game active')})

if __name__ == '__main__':
    # Allow external connections by setting host to '0.0.0.0'
    # Enable threading to support multiple concurrent users
    app.run(debug=True, host='0.0.0.0', port=8000, threaded=True)
