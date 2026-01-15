# To run this code you need to install the following dependencies:
# pip install google-genai

import base64
import os
from google import genai
from google.genai import types

def generate():
    client = genai.Client(
        api_key=os.environ.get("AIzaSyCZ4g85Ml2Jreet2R0giLNBRgaChYIY0aw"),
    )

    model = "gemini-3-pro-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text="""cách máy tính hoạt động"""),
            ],
        ),
    ]
    tools = [
        types.Tool(google_search=types.GoogleSearch()),
    ]
    
    # --- FIX STARTS HERE ---
    generate_content_config = types.GenerateContentConfig(
        # Python uses '=' for arguments, and 'snake_case' for the parameter name
        thinking_config=types.ThinkingConfig(
            include_thoughts=True
        ),
        tools=tools,
    )
    # --- FIX ENDS HERE ---

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        # Print parts safely
        if chunk.text:
            print(chunk.text, end="")

if __name__ == "__main__":
    generate()