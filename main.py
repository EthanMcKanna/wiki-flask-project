from flask import Flask, request, render_template, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import wikipedia
import requests
import sqlite3
from openai import OpenAI
import json
import os
from dotenv import load_dotenv

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
    (id INTEGER PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT)
''')
c.execute('''
    CREATE TABLE IF NOT EXISTS user_preferences
    (user_id INTEGER PRIMARY KEY, summary_complexity TEXT, custom_summary TEXT, FOREIGN KEY(user_id) REFERENCES users(id))
''')

conn.commit()

# User model for Flask-Login
class User(UserMixin):
    def __init__(self, id, email):
        self.id = id
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    c.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    if user:
        return User(user[0], user[1])
    return None

def generate_summary(query, text, user_preferences=None):
    system_message = "You are wikiGPT and your goal is to provide simple, coherent, and easily understandable summaries of the given wikipedia excerpts for a user query. You are to provide two summaries; a more advanced summary and a very basic explanation easily understandable by anyone of any age using more simple vocabulary. Use JSON to store the advanced and basic summaries in the following format: { 'advanced': '...', 'basic': '...' }"
    user_message = f"Here is a wikipedia excerpt for the query {query}: {text}"

    response = client.chat.completions.create(
        model="gpt-3.5-turbo-1106",
        response_format={ "type": "json_object" },
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message}
        ],
        temperature=1,
        max_tokens=256,
        top_p=1,
        frequency_penalty=0,
        presence_penalty=0
    )
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        c.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        if user and check_password_hash(user[2], password):
            user_obj = User(user[0], user[1])
            login_user(user_obj)
            return redirect(url_for('search_wikipedia'))
        else:
            flash('Invalid email or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('search_wikipedia'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        password_hash = generate_password_hash(password)
        try:
            c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
            conn.commit()
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Email already exists')
    return render_template('register.html')

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
def search_wikipedia():
    related_articles = []
    query = request.args.get('query') if request.method == 'GET' else request.form.get('query')

    user_id = current_user.get_id() if current_user.is_authenticated else None
    print(user_id)
    c.execute("SELECT summary_complexity FROM user_preferences WHERE user_id=?", (user_id,))
    user_preferences = c.fetchall()
    
    if user_preferences:
        # Extract the first element of the first tuple
        summary_complexity = user_preferences[0][0]
        print("User Preference:", summary_complexity)
    else:
        summary_complexity = None  # or a default value
        print("No preference set for user.")

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
