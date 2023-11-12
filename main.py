from flask import Flask, request, render_template
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

# Create a connection to the SQLite database
# Doesn't matter if the database doesn't yet exist
conn = sqlite3.connect('wiki_cache.db', check_same_thread=False)

c = conn.cursor()


c.execute('''
    CREATE TABLE IF NOT EXISTS api_cache
    (query TEXT PRIMARY KEY, wikipedia_summary TEXT, ai_summaries TEXT,  image_url TEXT)
''')

conn.commit()


def generate_summary(query, text):
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

@app.route('/', methods=['GET', 'POST'])
def search_wikipedia():
    if request.method == 'POST':
        query = request.form['query']
        query = query.strip().lower()
        
        # Check if the result is in the database
        c.execute("SELECT wikipedia_summary, ai_summaries, image_url FROM api_cache WHERE query=?", (query,))
        row = c.fetchone()
        
        if row is not None:
            wikipedia_summary, ai_summaries_json, image_url = row
            ai_summaries = json.loads(ai_summaries_json)
            print(ai_summaries)
        else:
            # If the result is not in the database, fetch it and store it
            try:
                # Search for the query and get the top result
                results = wikipedia.search(query)
                if results:
                    top_result = results[0]
                    
                    # Get the summary of the top result
                    wikipedia_summary = wikipedia.summary(top_result, auto_suggest=False)
                    ai_summaries = generate_summary(top_result, wikipedia_summary)
                    ai_summaries_json = json.dumps(ai_summaries)

                    image_link = f"https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2&prop=pageimages|pageterms&piprop=thumbnail&pithumbsize=100&pilicense=any&titles={top_result}"
                    image_url = extract_thumbnail_link(image_link)

                    if image_url is None:
                        image_url = "https://via.placeholder.com/150"

                    c.execute("INSERT OR REPLACE INTO api_cache VALUES (?, ?, ?, ?)", (query, wikipedia_summary, ai_summaries_json, image_url))
                    conn.commit()
                    
                else:
                    return "No results found for your query."
            except wikipedia.exceptions.DisambiguationError as e:
                first_option = e.options[0]
                wikipedia_summary = wikipedia.summary(first_option, auto_suggest=False)
                ai_summaries = generate_summary(query, wikipedia_summary)
                ai_summaries_json = json.dumps(ai_summaries)

                image_link = f"https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2&prop=pageimages|pageterms&piprop=thumbnail&pithumbsize=100&pilicense=any&titles={first_option}"
                image_url = extract_thumbnail_link(image_link)

                if image_url is None:
                    image_url = "https://via.placeholder.com/150"

                c.execute("INSERT INTO api_cache VALUES (?, ?, ?, ?)", (query, wikipedia_summary, ai_summaries_json, image_url))
                conn.commit()
                return render_template('results.html', summary=wikipedia_summary, ai_summary=ai_summaries, image_url=image_url, query=first_option.title())
            except wikipedia.exceptions.PageError:
                return "No page matches the query."
        return render_template('results.html', summary=wikipedia_summary, ai_summary=ai_summaries, image_url=image_url, query=query.title())
    return render_template('index.html')

if __name__ == "__main__":
    app.run(debug=True)
    # Close the database connection
    conn.close()
