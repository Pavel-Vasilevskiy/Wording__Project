let currentGuess = '';
let gameOver = false;
let submissionPending = false;
let gameReady = false;
let keyboardState = {}; // Track keyboard key states

// DOM elements
const newGameButton = document.getElementById('new-game');
const gameStatus = document.getElementById('game-status');
const triesCount = document.getElementById('tries-count');
const gameBoard = document.getElementById('game-board');

gameBoard.addEventListener('animationend', function(e) {
    if (e.animationName === 'guess-row-shake') {
        e.target.classList.remove('shake');
    }
});

// Sidebar elements
const mobileMenuBtn = document.getElementById('mobile-menu-btn');
const sidebar = document.getElementById('sidebar');
const gamesCountEl = document.getElementById('games-count');
const winRateEl = document.getElementById('win-rate');
const currentStreakEl = document.getElementById('current-streak');
const maxStreakEl = document.getElementById('max-streak');

// Initialize game board
function initializeGameBoard() {
    gameBoard.innerHTML = '';
    for (let i = 0; i < 6; i++) {
        const row = document.createElement('div');
        row.className = 'guess-row';
        row.id = `row-${i}`;
        
        for (let j = 0; j < 5; j++) {
            const letterBox = document.createElement('div');
            letterBox.className = 'letter-box';
            letterBox.id = `row-${i}-letter-${j}`;
            row.appendChild(letterBox);
        }
        
        gameBoard.appendChild(row);
    }
}

// Render the unsubmitted guess in the next empty board row.
function updateCurrentGuess() {
    const rowIndex = Number(triesCount.textContent);
    const row = gameOver ? null : document.getElementById(`row-${rowIndex}`);
    document.querySelectorAll('.guess-row').forEach(guessRow => {
        guessRow.classList.toggle('active', guessRow === row);
    });
    if (!row) return;

    const letters = currentGuess.split('');
    for (let i = 0; i < 5; i++) {
        document.getElementById(`row-${rowIndex}-letter-${i}`).textContent = letters[i] || '';
    }
}

// Physical keyboard uses the same handlers as the on-screen keyboard.
document.addEventListener('keydown', function(e) {
    if (!gameReady || gameOver || submissionPending || e.ctrlKey || e.metaKey || e.altKey) return;

    const target = e.target;
    if (target && typeof target.closest === 'function' &&
        target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
        return;
    }
    // Preserve native activation for focused buttons/links while still letting
    // physical letters and Backspace work after clicking a game control.
    if (target && typeof target.closest === 'function' &&
        target.closest('button, a') && (e.key === 'Enter' || e.key === ' ')) return;

    if (e.key === 'Backspace') {
        e.preventDefault();
        handleBackspace();
    } else if (e.key === 'Enter') {
        e.preventDefault();
        submitGuess();
    } else if (/^[a-z]$/i.test(e.key)) {
        e.preventDefault();
        handleKeyPress(e.key.toUpperCase());
    }
});

function submitGuess() {
    if (!gameReady || submissionPending) return;

    if (gameOver) {
        gameStatus.textContent = 'Game is over! Start a new game.';
        return;
    }

    if (currentGuess.length !== 5) {
        gameStatus.textContent = 'Please enter a 5-letter word!';
        return;
    }

    submissionPending = true;
    fetch('/guess', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ guess: currentGuess })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            gameStatus.textContent = data.error;
            if (data.error === 'Not in word list') {
                const activeRow = document.getElementById(`row-${Number(triesCount.textContent)}`);
                if (activeRow) activeRow.classList.remove('shake');
                // Force a reflow so consecutive invalid guesses restart the animation.
                if (activeRow) {
                    void activeRow.offsetWidth;
                    activeRow.classList.add('shake');
                }
            }
            return;
        }

        // Update game board with the result
        updateGameBoard(data.result, data.tries - 1);
        
        // Update keyboard state with the result
        updateKeyboardState(data.result);
        
        // Update tries count
        triesCount.textContent = data.tries;

        // Check game status
        if (data.won) {
            gameStatus.textContent = `🎉 Congratulations! You won in ${data.tries} tries!`;
            gameOver = true;
            
            // Show updated user stats if available
            if (data.user_stats) {
                showStatsUpdate(data.user_stats);
            }
        } else if (data.game_over) {
            gameStatus.textContent = `😞 Game over! The word was: ${data.target_word}`;
            gameOver = true;
            
            // Show updated user stats if available
            if (data.user_stats) {
                showStatsUpdate(data.user_stats);
            }
        } else {
            gameStatus.textContent = `Try ${data.tries}/6`;
        }

        // Clear the active row after an accepted guess.
        currentGuess = '';
        updateCurrentGuess();
    })
    .catch(error => {
        console.error('Error:', error);
        gameStatus.textContent = 'An error occurred. Please try again.';
    })
    .finally(() => {
        submissionPending = false;
    });
}

// Update game board with guess result
function updateGameBoard(result, rowIndex) {
    for (let i = 0; i < 5; i++) {
        const letterBox = document.getElementById(`row-${rowIndex}-letter-${i}`);
        letterBox.textContent = result[i].letter;
        letterBox.className = `letter-box ${result[i].status}`;
    }
}

// New game
newGameButton.addEventListener('click', function() {
    if (!gameReady || submissionPending) return;

    fetch('/new_game', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        // Reset game state
        gameOver = false;
        currentGuess = '';
        triesCount.textContent = '0';
        gameStatus.textContent = 'New game started! Good luck!';
        
        // Reset displays
        initializeGameBoard();
        resetKeyboard();
        updateCurrentGuess();
        gameReady = true;
    })
    .catch(error => {
        console.error('Error:', error);
        gameStatus.textContent = 'Error starting new game.';
    });
});

// Pointer clicks should not leave a game key focused. Keyboard activation
// keeps its normal focus so Enter/Space remain accessible.
function releasePointerFocus(event) {
    if (event.detail > 0 && event.currentTarget && typeof event.currentTarget.blur === 'function') {
        event.currentTarget.blur();
    }
}

// Initialize keyboard
function initializeKeyboard() {
    // Initialize all keys as unused
    const keys = 'QWERTYUIOPASDFGHJKLZXCVBNM';
    for (let key of keys) {
        keyboardState[key] = 'unused';
    }
    updateKeyboardDisplay();
    
    // Add event listeners to keyboard keys
    const keyElements = document.querySelectorAll('.key[data-key]');
    keyElements.forEach(key => {
        key.addEventListener('click', (event) => {
            const letter = key.getAttribute('data-key');
            handleKeyPress(letter);
            releasePointerFocus(event);
        });
    });
    
    // Add event listeners for special keys
    document.getElementById('key-enter').addEventListener('click', (event) => {
        submitGuess();
        releasePointerFocus(event);
    });
    
    document.getElementById('key-backspace').addEventListener('click', (event) => {
        handleBackspace();
        releasePointerFocus(event);
    });
}

// Handle key press from virtual keyboard
function handleKeyPress(letter) {
    if (!gameReady || gameOver) return;
    
    if (currentGuess.length < 5) {
        currentGuess += letter.toUpperCase();
        updateCurrentGuess();
    }
}

// Handle backspace
function handleBackspace() {
    if (!gameReady || gameOver) return;
    
    if (currentGuess.length > 0) {
        currentGuess = currentGuess.slice(0, -1);
        updateCurrentGuess();
    }
}

// Update keyboard display based on key states
function updateKeyboardDisplay() {
    for (let letter in keyboardState) {
        const keyElement = document.querySelector(`[data-key="${letter}"]`);
        if (keyElement) {
            // Remove all status classes
            keyElement.classList.remove('correct', 'present', 'absent', 'unused');
            
            // Add current status class
            switch (keyboardState[letter]) {
                case 'correct':
                    keyElement.classList.add('correct');
                    break;
                case 'present':
                    keyElement.classList.add('present');
                    break;
                case 'absent':
                    keyElement.classList.add('absent');
                    break;
                default:
                    keyElement.classList.add('unused');
            }
        }
    }
}

// Update keyboard state based on guess result
function updateKeyboardState(result) {
    for (let letterData of result) {
        const letter = letterData.letter;
        const status = letterData.status;
        
        // Only update if the new status is "better" than the current one
        // Priority: correct > present > absent > unused
        const currentStatus = keyboardState[letter];
        
        if (status === 'green' && currentStatus !== 'correct') {
            keyboardState[letter] = 'correct';
        } else if (status === 'yellow' && currentStatus !== 'correct' && currentStatus !== 'present') {
            keyboardState[letter] = 'present';
        } else if (status === 'gray' && currentStatus === 'unused') {
            keyboardState[letter] = 'absent';
        }
    }
    
    updateKeyboardDisplay();
}

// Reset keyboard state for new game
function resetKeyboard() {
    const keys = 'QWERTYUIOPASDFGHJKLZXCVBNM';
    for (let key of keys) {
        keyboardState[key] = 'unused';
    }
    updateKeyboardDisplay();
}

// Function to show updated user stats after game completion
function showStatsUpdate(userStats) {
    // Update sidebar statistics
    updateSidebarStats(userStats);
    
    // Update user stats in the header if elements exist (legacy support)
    const userStatsElements = document.querySelectorAll('.user-stats span');
    if (userStatsElements.length >= 3) {
        userStatsElements[0].textContent = `Games: ${userStats.games_played}`;
        userStatsElements[1].textContent = `Win Rate: ${userStats.win_percentage}%`;
        userStatsElements[2].textContent = `Streak: ${userStats.current_streak}`;
    }
    
    // Show a brief stats update notification
    const notification = document.createElement('div');
    notification.className = 'stats-notification';
    notification.innerHTML = `
        <div class="stats-update">
            <h3>📊 Stats Updated!</h3>
            <p>Games: ${userStats.games_played} | Win Rate: ${userStats.win_percentage}% | Streak: ${userStats.current_streak}</p>
        </div>
    `;
    
    // Add styles for the notification
    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: #1a1a1b;
        border: 2px solid #6aaa64;
        border-radius: 8px;
        padding: 15px;
        color: white;
        z-index: 1000;
        max-width: 300px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        animation: slideIn 0.3s ease-out;
    `;
    
    // Add animation styles
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .stats-update h3 { margin: 0 0 8px 0; color: #6aaa64; font-size: 1.1em; }
        .stats-update p { margin: 0; color: #c9b458; font-size: 0.9em; }
    `;
    document.head.appendChild(style);
    
    document.body.appendChild(notification);
    
    // Remove notification after 4 seconds
    setTimeout(() => {
        if (notification.parentNode) {
            notification.remove();
        }
    }, 4000);
}

// Mobile menu functionality
function initializeMobileMenu() {
    if (mobileMenuBtn && sidebar) {
        mobileMenuBtn.addEventListener('click', function() {
            sidebar.classList.toggle('open');
        });
        
        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', function(e) {
            if (window.innerWidth <= 480) {
                if (!sidebar.contains(e.target) && !mobileMenuBtn.contains(e.target)) {
                    sidebar.classList.remove('open');
                }
            }
        });
    }
}

// Update sidebar statistics
function updateSidebarStats(stats) {
    if (gamesCountEl) gamesCountEl.textContent = stats.games_played || 0;
    if (winRateEl) winRateEl.textContent = `${stats.win_percentage || 0}%`;
    if (currentStreakEl) currentStreakEl.textContent = stats.current_streak || 0;
    if (maxStreakEl) maxStreakEl.textContent = stats.max_streak || 0;
}

// Initialize mobile menu when page loads
document.addEventListener('DOMContentLoaded', function() {
    initializeMobileMenu();
});

// Initialize the game board on page load
initializeGameBoard();
initializeKeyboard();
gameStatus.textContent = 'Loading your game...';

fetch('/game_state')
    .then(response => {
        if (!response.ok) throw new Error('Could not load the saved game');
        return response.json();
    })
    .then(state => {
        initializeGameBoard();
        resetKeyboard();

        state.guesses.forEach((result, rowIndex) => {
            updateGameBoard(result, rowIndex);
            updateKeyboardState(result);
        });

        triesCount.textContent = state.tries;
        currentGuess = '';
        gameOver = state.game_over;
        updateCurrentGuess();
        if (state.won) {
            gameStatus.textContent = `🎉 Congratulations! You won in ${state.tries} tries!`;
        } else if (state.game_over) {
            gameStatus.textContent = `😞 Game over! The word was: ${state.target_word}`;
        } else if (state.tries > 0) {
            gameStatus.textContent = `Try ${state.tries}/6`;
        } else {
            gameStatus.textContent = 'Welcome to Wordle! Enter a 5-letter word to start.';
        }

        gameReady = true;
    })
    .catch(error => {
        console.error('Error restoring game:', error);
        gameStatus.textContent = 'Could not load your game. Please reload the page.';
    });
