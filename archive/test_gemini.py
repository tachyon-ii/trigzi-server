#!/usr/bin/env python3
import requests
import json

def list_available_models(api_key):
    """
    Fetch all models available to this API key.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    print(f"--- Listing Available Models ---")
    try:
        response = requests.get(url)
        if response.status_code == 200:
            models = response.json().get('models', [])
            for m in models:
                print(f" - {m['name']}")
        else:
            print(f"Failed. Status: {response.status_code}")
    except Exception as e:
        print(f"Error: {e}")

def check_gemini_key(api_key, model_name="gemini-2.5-flash"):
    """
    Verifies API key and tests the new structured JSON prompt.
    Using v1beta which is required for the response_mime_type field in REST.
    """
    # Switching back to v1beta for better structured output support
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    headers = {'Content-Type': 'application/json'}
    
    # Testing with a real-world scenario (Menthol Lozenges)
    test_ocr = "Original Extra Strong Menthol Flavour Lozenges. Ingredients: Sugar, Glucose Syrup, Menthol, Eucalyptus Oil, Liquorice Extract, Capsicum Tincture."
    test_profile = "No specific allergies."
    
    prompt = f"""
    Analyze this OCR text for food safety. User profile: {test_profile}.
    Return strictly JSON with: verdict, summary, warnings (list), flaggedIngredients (list), detailedReason.
    OCR: {test_ocr}
    """
    
    # In v1beta REST API, response_mime_type is the standard key inside generationConfig
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }

    print(f"\n--- Testing Structured Analysis (Model: {model_name}) ---")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        
        if response.status_code == 200:
            result = response.json()
            raw_text = result['candidates'][0]['content']['parts'][0]['text']
            parsed = json.loads(raw_text)
            print("SUCCESS! Structured JSON received:")
            print(json.dumps(parsed, indent=2))
        else:
            print(f"FAILED! Status: {response.status_code}")
            print(response.text)
            
    except Exception as e:
        print(f"ERROR: {str(e)}")

if __name__ == "__main__":
    MY_KEY = ""  # needs key here
    check_gemini_key(MY_KEY)
