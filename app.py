from datetime import datetime
import os
import socket
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pymongo import MongoClient
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

# ==========================
# Load Environment Variables
# ==========================
load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
mongo_uri = os.getenv("MONGO_URL")

if not groq_api_key:
    raise ValueError("GROQ_API_KEY not found in environment variables")

if not mongo_uri:
    raise ValueError("MONGO_URL not found in environment variables")

# ==========================
# MongoDB Setup
# ==========================
client = MongoClient(mongo_uri)
db = client["study_bot"]
collection = db["conversations"]

# ==========================
# FastAPI App
# ==========================
app = FastAPI(title="Study Bot API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development only
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True
)

# ==========================
# Pydantic Models
# ==========================
class ChatRequest(BaseModel):
    user_id: str
    question: str


class ChatResponse(BaseModel):
    response: str
    user_id: str
    timestamp: datetime


class MessageHistory(BaseModel):
    role: str
    message: str
    timestamp: datetime


# ==========================
# Study Assistant System Prompt
# ==========================
SYSTEM_PROMPT = """
You are a strict Academic Study Assistant.

Rules:
- Only answer academic questions.
- If question is not study-related, respond:
  "I am designed for academic learning support only."

Teaching Method:
1. Define the concept briefly.
2. Explain clearly in simple terms.
3. Show step-by-step reasoning when solving problems.
4. Provide an example if useful.
5. Encourage understanding.

Tone:
Professional, structured, clear.
No emojis.
No casual conversation.
"""

# ==========================
# LangChain Setup
# ==========================
prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("placeholder", "{history}"),
    ("human", "{question}")
])

chat = ChatGroq(
    groq_api_key=groq_api_key,
    model="llama-3.3-70b-versatile",
    temperature=0.3  # Lower temperature for academic precision
)

chain = prompt | chat


# ==========================
# Helper Functions
# ==========================

def get_chat_history(user_id: str, limit: int = 6):
    """
    Retrieve recent chat history and format correctly for LangChain
    """
    try:
        chats = collection.find({"user_id": user_id}) \
                          .sort("timestamp", -1) \
                          .limit(limit * 2)

        chats = list(chats)[::-1]  # Oldest first
        history = []

        for chat_doc in chats:
            role = chat_doc["role"]
            message = chat_doc["message"]

            if role == "user":
                history.append(("human", message))
            elif role == "assistant":
                history.append(("ai", message))

        return history

    except Exception as e:
        print(f"Error fetching history: {e}")
        return []


def save_message(user_id: str, role: str, message: str):
    """
    Save message to MongoDB
    """
    try:
        collection.insert_one({
            "user_id": user_id,
            "role": role,
            "message": message,
            "timestamp": datetime.utcnow()
        })
    except Exception as e:
        print(f"Error saving message: {e}")
        raise HTTPException(status_code=500, detail="Database error")


# ==========================
# Routes
# ==========================

@app.get("/")
def home():
    return {
        "message": "Study Bot API is running",
        "status": "active",
        "version": "1.0",
        "endpoints": {
            "chat": "POST /chat",
            "history": "GET /history/{user_id}",
            "clear_history": "DELETE /history/{user_id}",
            "stats": "GET /stats/{user_id}"
        }
    }


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    try:
        # 1️⃣ Get previous history FIRST (avoid duplication bug)
        history = get_chat_history(request.user_id, limit=6)

        # 2️⃣ Call LLM
        response = chain.invoke({
            "history": history,
            "question": request.question
        })

        ai_response = response.content

        # 3️⃣ Save both messages AFTER response
        save_message(request.user_id, "user", request.question)
        save_message(request.user_id, "assistant", ai_response)

        return ChatResponse(
            response=ai_response,
            user_id=request.user_id,
            timestamp=datetime.utcnow()
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat processing error: {str(e)}")


@app.get("/history/{user_id}")
def get_user_history(user_id: str):
    try:
        chats = collection.find({"user_id": user_id}).sort("timestamp", -1)

        messages = []
        for chat in chats:
            messages.append(MessageHistory(
                role=chat["role"],
                message=chat["message"],
                timestamp=chat["timestamp"]
            ))

        return {
            "user_id": user_id,
            "total_messages": len(messages),
            "messages": messages
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving history: {str(e)}")


@app.delete("/history/{user_id}")
def clear_history(user_id: str):
    try:
        result = collection.delete_many({"user_id": user_id})
        return {
            "message": f"Deleted {result.deleted_count} messages",
            "user_id": user_id
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error clearing history: {str(e)}")


@app.get("/stats/{user_id}")
def get_stats(user_id: str):
    try:
        total = collection.count_documents({"user_id": user_id})
        user_msgs = collection.count_documents({"user_id": user_id, "role": "user"})
        assistant_msgs = collection.count_documents({"user_id": user_id, "role": "assistant"})

        return {
            "user_id": user_id,
            "total_messages": total,
            "user_messages": user_msgs,
            "assistant_messages": assistant_msgs
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting stats: {str(e)}")


# ==========================
# Run Local Server
# ==========================
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    preferred_port = int(os.getenv("PORT", "8000"))

    port = preferred_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                break
        port += 1

    if port != preferred_port:
        print(f"Port {preferred_port} is in use. Starting on available port {port}.")

    print(f"Open in browser: http://{host}:{port}/")
    print(f"Swagger UI: http://{host}:{port}/docs")

    uvicorn.run(app, host=host, port=port)