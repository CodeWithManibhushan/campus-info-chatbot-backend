import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure Gemini with API key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def get_ai_response(user_message: str) -> str:
    try:
        # Latest model (compatible with v1 API)
        # model = genai.GenerativeModel(model_name="gemini-1.5-flash")
        model = genai.GenerativeModel(model_name="gemini-pro")

        # Generate AI content
        response = model.generate_content(user_message)

        # Return the response text
        if hasattr(response, 'text'):
            return response.text
        else:
            return str(response)
    except Exception as e:
        return f"⚠️ AI Error: {e}"
