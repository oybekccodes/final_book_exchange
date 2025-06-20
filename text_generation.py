import google.generativeai as genai

genai.configure(api_key="AIzaSyDwn13KMvT9yIfececfQ_xbBfxe2gpM0-4")  # Replace with your real key

model = genai.GenerativeModel("gemini-2.0-flash")

def generate_description(prompt):
    try:
        response = model.generate_content([prompt])
        return response.text
    except Exception as e:
        return f"Error: {e}"
