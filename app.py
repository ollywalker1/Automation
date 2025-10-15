import os
import google.generativeai as genai
from flask import Flask, render_template, request, jsonify
from bs4 import BeautifulSoup
import requests
import json

app = Flask(__name__)

# Configure the Gemini API
# IMPORTANT: Set your API key as an environment variable
# export GEMINI_API_KEY="YOUR_API_KEY"
api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    # This check is crucial for production. We will not bypass it.
    # The app will not start without a valid key.
    raise ValueError("Gemini API key not found. Please set the GEMINI_API_KEY environment variable.")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-pro')
chat_session = model.start_chat()


# In-memory store for conversation state.
# NOTE: This is a simple implementation for a single user.
# For a production application, use a session-based approach to handle multiple users.
conversation_state = {
    "step": 1,
    "url": None,
    "criteria": None,
    "extracted_resorts": [],
    "page_offset": 0
}

@app.route('/')
def index():
    # Reset state on new session
    global conversation_state
    conversation_state = {
        "step": 1,
        "url": None,
        "criteria": None,
        "extracted_resorts": [],
        "page_offset": 0
    }
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    global conversation_state
    data = request.get_json()
    user_message = data.get('message')

    response_message = ""

    if conversation_state["step"] == 1:
        # Step 1: Get URL
        conversation_state["url"] = user_message
        conversation_state["step"] = 2
        response_message = "What are the specific criteria for the resorts you want me to find? (e.g., number of stars, location, amenities)."

    elif conversation_state["step"] == 2:
        # Step 2: Get Criteria and perform first extraction
        conversation_state["criteria"] = user_message
        conversation_state["step"] = 3
        response_message = """
        I will capture the following critical data points for each resort:
        <ul>
            <li>Resort Name</li>
            <li>Country</li>
            <li>Description</li>
            <li>Star Rating</li>
            <li>Price</li>
            <li>Main Picture (Image)</li>
        </ul>
        All data will be presented in a clean, organized table format.
        I will now extract information for the first twenty resorts that match your criteria.
        Once I've provided the first twenty resorts, you can type <b>CONTINUE</b> to get the next twenty. If you have finished, type <b>FINISH</b>.
        """
        # Trigger the first extraction
        resorts_html = extract_resorts()
        response_message += resorts_html

    elif user_message.upper() == 'CONTINUE':
        # Subsequent extractions
        conversation_state["page_offset"] += 20
        resorts_html = extract_resorts()
        if not resorts_html or "No more resorts found" in resorts_html:
            response_message = "No more resorts matching the criteria were found."
        else:
            response_message = resorts_html

    elif user_message.upper() == 'FINISH':
        # Consolidate and finish
        response_message = "Consolidating all extracted data...<br>"
        response_message += consolidate_resorts()
        # Reset for a new query
        conversation_state = { "step": 1, "url": None, "criteria": None, "extracted_resorts": [], "page_offset": 0 }

    else:
        # Fallback for unexpected messages during an ongoing extraction process
        response_message = "Please use CONTINUE to get more results or FINISH to complete the process."

    return jsonify({'response': response_message})


def extract_resorts():
    """
    Fetches HTML from the target URL and uses Gemini to extract resort data
    directly from the HTML content, returning the results as an HTML table.
    This approach avoids using exec() and is much safer.
    """
    global conversation_state

    try:
        # Fetch the HTML content from the URL
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
        page_response = requests.get(conversation_state['url'], headers=headers, timeout=10)
        page_response.raise_for_status() # Raise an exception for bad status codes

        # Use BeautifulSoup to get the main text content, reducing the token count for Gemini
        soup = BeautifulSoup(page_response.text, 'html.parser')
        html_content = str(soup.body) # Pass the body content to Gemini

        # Create a more robust prompt for data extraction
        prompt = f"""
        Analyze the following HTML content and extract data for holiday resorts based on these criteria: '{conversation_state['criteria']}'.

        Extract the following fields for each resort:
        - Resort Name
        - Country
        - Description
        - Star Rating
        - Price
        - Main Picture (the full URL to the image)

        Follow these rules:
        1. Identify the top 20 resorts from the HTML that match the criteria, starting from result number {conversation_state['page_offset']}.
        2. Do NOT include any resorts from this list of already extracted names: {", ".join([r['Resort Name'] for r in conversation_state['extracted_resorts']])}.
        3. If a field (like 'Price' or 'Star Rating') is not found for a resort, set its value to "N/A".
        4. Return the data as a single, valid JSON array of objects. Do not include any text or explanations outside of the JSON array.

        Example of the exact output format expected:
        [
            {{
                "Resort Name": "Example Resort 1",
                "Country": "Spain",
                "Description": "A beautiful resort...",
                "Star Rating": "4",
                "Price": "$200/night",
                "Main Picture": "https://example.com/image1.jpg"
            }}
        ]

        HTML Content to analyze is provided below:
        ---
        {html_content}
        """

        # Let Gemini extract the data
        response = chat_session.send_message(prompt)
        # Clean up the response to ensure it's valid JSON
        json_text = response.text.strip().replace("```json", "").replace("```", "")

        # Parse the JSON response
        newly_extracted_resorts = json.loads(json_text)

        if not newly_extracted_resorts:
            return "<p>No more resorts found matching your criteria.</p>"

        # Add to our state and prevent duplicates
        for resort in newly_extracted_resorts:
            if resort["Resort Name"] not in [r["Resort Name"] for r in conversation_state["extracted_resorts"]]:
                conversation_state["extracted_resorts"].append(resort)

        # Format as an HTML table
        return create_html_table(newly_extracted_resorts)

    except requests.exceptions.RequestException as e:
        return f"<p>Sorry, I couldn't access the website at that URL. Error: {e}</p>"
    except json.JSONDecodeError:
        return "<p>Sorry, I received an invalid format from the extraction service. I can't process the results.</p>"
    except Exception as e:
        print(f"An error occurred during extraction: {e}")
        return f"<p>Sorry, an unexpected error occurred: {e}</p>"


def create_html_table(resorts):
    if not resorts:
        return ""

    table = "<table>"
    # Header
    table += "<tr>"
    for key in resorts[0].keys():
        table += f"<th>{key.replace('_', ' ').title()}</th>"
    table += "</tr>"
    # Rows
    for resort in resorts:
        table += "<tr>"
        for key, value in resort.items():
            if key == "Main Picture" and str(value).startswith('http'):
                table += f'<td><img src="{value}" alt="{resort.get("Resort Name", "Resort Image")}" width="100" style="max-height:100px;object-fit:cover;"></td>'
            else:
                table += f"<td>{value}</td>"
        table += "</tr>"
    table += "</table>"
    return table

def consolidate_resorts():
    """Groups all extracted resorts by country and returns a final HTML table."""

    if not conversation_state["extracted_resorts"]:
        return "<p>No resort data was collected to consolidate.</p>"

    # Group by country
    grouped_resorts = {}
    for resort in conversation_state["extracted_resorts"]:
        country = resort.get("Country", "Unknown")
        if country not in grouped_resorts:
            grouped_resorts[country] = []
        grouped_resorts[country].append(resort)

    # Create final HTML
    final_html = "<h2>All Extracted Resorts by Country</h2>"
    for country, resorts in sorted(grouped_resorts.items()):
        final_html += f"<h3>{country}</h3>"
        final_html += create_html_table(resorts)

    return final_html


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
