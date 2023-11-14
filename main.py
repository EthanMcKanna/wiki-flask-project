from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
import traceback
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import wikipedia
import requests
import sqlite3
from openai import OpenAI
import json
import os
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer
from flask_mail import Mail, Message

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)
FLASK_KEY = os.getenv('FLASK_KEY')
app.secret_key = FLASK_KEY

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Initialize Flask-Mail and URLSafeTimedSerializer
app.config.from_pyfile('config.cfg')
mail = Mail(app)
s = URLSafeTimedSerializer(app.secret_key)

# Create a connection to the SQLite database
conn = sqlite3.connect('wiki_cache.db', check_same_thread=False)
c = conn.cursor()

# Create tables
c.execute('''
    CREATE TABLE IF NOT EXISTS api_cache
    (article_title TEXT PRIMARY KEY, wikipedia_summary TEXT, related_topics TEXT, ai_summaries TEXT, image_url TEXT, queries TEXT)
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS users
    (id INTEGER PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT, confirmed INTEGER DEFAULT 0)
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS user_preferences
    (user_id INTEGER PRIMARY KEY, summary_complexity TEXT, custom_summary TEXT, FOREIGN KEY(user_id) REFERENCES users(id))
''')

conn.commit()

# User model for Flask-Login
class User(UserMixin):
    def __init__(self, id, email, confirmed):
        self.id = id
        self.email = email
        self.confirmed = confirmed

@login_manager.user_loader
def load_user(user_id):
    c.execute("SELECT id, email, confirmed FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    if user:
        return User(user[0], user[1], user[2])
    return None

def generate_summary(query, text, user_preferences=None):
    system_message = "You are a service known as YouSummarize and your goal is to provide simple, coherent, and easily understandable summaries of the given wikipedia excerpts for a user query. You are to provide two summaries; a more advanced summary and a very basic explanation easily understandable by anyone of any age using more simple vocabulary. Use JSON to store the advanced and basic summaries in the following format: { 'advanced': '...', 'basic': '...' }"
    user_message = f"Here is a wikipedia excerpt for the query {query}: {text}"

    response = client.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        response_format={ "type": "json_object" },
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ],
        temperature=1,
        max_tokens=512,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )

    print("DEBUG")
    print(response.choices[0].message.content)
    ai_summaries = json.loads(response.choices[0].message.content)
    return ai_summaries

def get_related_articles(query):
    related_articles = wikipedia.search(query, results=5)
    return related_articles

def extract_thumbnail_link(api_url):
    response = requests.get(api_url)
    
    if response.status_code == 200:
        data = response.json()
        
        try:
            thumbnail_link = data['query']['pages'][0]['thumbnail']['source']
            return thumbnail_link
        except KeyError:
            print("Thumbnail link not found in the response.")
            return None
    else:
        print(f"Error: {response.status_code}")
        return None

@app.route('/suggest')
def suggest():
    query = request.args.get('query', '').strip()
    suggestions = []
    if query:
        try:
            suggestions = wikipedia.search(query, results=5)  # Adjust the number of results as needed
        except Exception as e:
            print("Error occurred in autosuggest:", traceback.format_exc())
    return jsonify(suggestions)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        c.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        if user and check_password_hash(user[2], password):
            user_obj = User(user[0], user[1], user[2])
            login_user(user_obj)
            return redirect(url_for('search_wikipedia'))
        else:
            flash('Invalid email or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        password_hash = generate_password_hash(password)
        try:
            c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
            conn.commit()

            # Generate a confirmation token and send it to the user
            token = s.dumps(email, salt='email-confirm')
            msg = Message('Verify your YouSummarize Account', sender=("YouSummarize", "YouSummarize@gmail.com"), recipients=[email])
            link = url_for('confirm_email', token=token, _external=True)
            msg.body = f'Thanks for creating a YouSummarize account! \n\nClick this link to complete sign up: {link}'
            mail.send(msg)

            return redirect(url_for('email_verification_required'))
        except sqlite3.IntegrityError:
            flash('Email already exists')
    return render_template('register.html')

@app.route('/confirm_email/<token>')
def confirm_email(token):
    try:
        email = s.loads(token, salt='email-confirm', max_age=3600)
    except:
        flash('The confirmation link is invalid or has expired.')
        return redirect(url_for('login'))

    c.execute("UPDATE users SET confirmed = 1 WHERE email = ?", (email,))
    conn.commit()
    flash('Email confirmed. Please login.')
    return redirect(url_for('login'))

@app.route('/resend_verification_email')
@login_required
def resend_verification_email():
    # Generate a new confirmation token
    token = s.dumps(current_user.email, salt='email-confirm')

    # Send the email
    msg = Message('Verify your YouSummarize Account', sender=("YouSummarize", "YouSummarize@gmail.com"), recipients=[current_user.email])
    link = url_for('confirm_email', token=token, _external=True)
    msg.body = f'Thanks for creating a YouSummarize account! \n\nClick this link to complete sign up: {link}'
    mail.send(msg)

    flash('A new confirmation email has been sent.')
    return redirect(url_for('email_verification_required'))

@app.route('/email_verification_required')
@login_required
def email_verification_required():
    return render_template('email_verification_required.html')

@app.route('/user/settings', methods=['GET', 'POST'])
@login_required
def user_settings():
    if request.method == 'POST':
        summary_complexity = request.form['summary_complexity']
        user_id = current_user.get_id()

        # Check if user preferences already exist
        c.execute("SELECT 1 FROM user_preferences WHERE user_id = ?", (user_id,))
        exists = c.fetchone()

        try:
            if exists:
                # Update existing record
                c.execute("UPDATE user_preferences SET summary_complexity = ? WHERE user_id = ?", (summary_complexity, user_id))
            else:
                # Insert new record
                c.execute("INSERT INTO user_preferences (user_id, summary_complexity) VALUES (?, ?)", (user_id, summary_complexity))
            conn.commit()
            flash('Settings updated successfully!')
        except sqlite3.Error as e:
            flash(f"An error occurred: {e}")
            print(f"An error occurred: {e}")

        return redirect(url_for('user_settings'))

    return render_template('user_settings.html')

@app.route('/', methods=['GET', 'POST'])
@login_required
def search_wikipedia():
    if not current_user.confirmed:
        flash('Please confirm your email before sending queries.')
        return redirect(url_for('email_verification_required'))
    related_articles = []
    top_result = None
    query = request.args.get('query') if request.method == 'GET' else request.form.get('query')

    user_id = current_user.get_id() if current_user.is_authenticated else None
    c.execute("SELECT summary_complexity FROM user_preferences WHERE user_id=?", (user_id,))
    user_preferences = c.fetchall()
    
    if user_preferences:
        # Extract the first element of the first tuple
        summary_complexity = user_preferences[0][0]
    else:
        summary_complexity = None  # or a default value

    if query:
        query = query.strip().lower()
        results = wikipedia.search(query)
        if results:
            top_result = results[0]

            c.execute("SELECT wikipedia_summary, ai_summaries, image_url, related_topics, queries FROM api_cache WHERE article_title=?", (top_result,))
            row = c.fetchone()

            if row:
                wikipedia_summary, ai_summaries_json, image_url, related_articles_string, cached_queries = row
                ai_summaries = json.loads(ai_summaries_json)
                if related_articles_string:
                    related_articles = related_articles_string.split(", ")
                else:
                    related_articles = get_related_articles(top_result)

                updated_queries = cached_queries + ", " + query if cached_queries else query
                c.execute("UPDATE api_cache SET queries=? WHERE article_title=?", (updated_queries, top_result))
            else:
                try:
                    wikipedia_summary = wikipedia.summary(top_result, auto_suggest=False)
                except wikipedia.exceptions.DisambiguationError as e:
                    top_result = e.options[0]
                    wikipedia_summary = wikipedia.summary(top_result, auto_suggest=False)
                
                ai_summaries = generate_summary(top_result, wikipedia_summary)
                ai_summaries_json = json.dumps(ai_summaries)

                related_articles = get_related_articles(top_result)
                related_articles_string = ", ".join(related_articles)

                image_link = f"https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2&prop=pageimages|pageterms&piprop=thumbnail&pithumbsize=100&pilicense=any&titles={top_result}"
                image_url = extract_thumbnail_link(image_link)

                c.execute("INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?, ?, ?, ?)", (top_result, wikipedia_summary, related_articles_string, ai_summaries_json, image_url, query))
            conn.commit()
        else:
            flash("No results found for your query.")
            return render_template('index.html')

        return render_template('results.html', summary=wikipedia_summary, ai_summary=ai_summaries, image_url=image_url, query=top_result, related_articles=related_articles, summary_complexity=summary_complexity)
    return render_template('index.html')

if __name__ == "__main__":
    app.run(debug=True)
