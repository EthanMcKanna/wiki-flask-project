from flask import Flask, request, render_template
import wikipedia
import requests


app = Flask(__name__)

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
        try:
            # Search for the query and get the top result
            results = wikipedia.search(query)
            if results:
                top_result = results[0]
                
                # Get the summary of the top result
                summary = wikipedia.summary(top_result, auto_suggest=False)

                image_link = f"https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2&prop=pageimages|pageterms&piprop=thumbnail&pithumbsize=100&pilicense=any&titles={top_result}"
                image_url = extract_thumbnail_link(image_link)
                # If there are images, use, otherwise use a placeholder

                if image_url is None:
                    image_url = "https://via.placeholder.com/150"

                
                return render_template('results.html', summary=summary,image_url=image_url)
            else:
                return "No results found for your query."
        except wikipedia.exceptions.DisambiguationError as e:
            first_option = e.options[0]
            summary = wikipedia.summary(first_option)
            return render_template('results.html', summary=summary)
        except wikipedia.exceptions.PageError:
            return "No page matches the query."
    return render_template('index.html')

if __name__ == "__main__":
    app.run(debug=True)