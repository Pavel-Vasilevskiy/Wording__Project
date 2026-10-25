from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User model for storing user information and authentication"""
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    picture = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Game statistics
    games_played = db.Column(db.Integer, default=0)
    games_won = db.Column(db.Integer, default=0)
    current_streak = db.Column(db.Integer, default=0)
    max_streak = db.Column(db.Integer, default=0)
    
    # Relationship to game sessions
    game_sessions = db.relationship('GameSession', backref='user', lazy=True)
    
    def __repr__(self):
        return f'<User {self.name}>'
    
    @property
    def win_percentage(self):
        if self.games_played == 0:
            return 0
        return round((self.games_won / self.games_played) * 100, 1)
    
    def update_stats(self, won, tries):
        """Update user statistics after a game; the caller commits the transaction."""
        self.games_played += 1
        if won:
            self.games_won += 1
            self.current_streak += 1
            if self.current_streak > self.max_streak:
                self.max_streak = self.current_streak
        else:
            self.current_streak = 0

class GameSession(db.Model):
    """Model for storing individual game sessions"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_word = db.Column(db.String(5), nullable=False)
    guesses = db.Column(db.Text)  # JSON string of guesses
    won = db.Column(db.Boolean, default=False)
    tries_used = db.Column(db.Integer, default=0)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<GameSession {self.id}: {self.target_word}>'
