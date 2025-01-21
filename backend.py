# backend.py
import os
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import threading
from queue import Queue
import logging
import sys
import traceback

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain.memory import ChatMessageHistory
from langsmith import traceable

# Enhanced logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('chatbot_backend.log')
    ]
)
logger = logging.getLogger(__name__)

# Request/Response models
class ChatRequest(BaseModel):
    message: str
    session_id: str
    metadata: Optional[Dict[str, Any]] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str

class ChatbotState:
    def __init__(self, llm, chain):
        self.llm = llm
        self.chain = chain

    @traceable(run_type="llm_chain")
    async def get_response(self, message: str, session_id: str, chat_sessions: Dict[str, ChatMessageHistory], metadata: Optional[Dict[str, Any]] = None) -> str:
        """Get response from the chatbot"""
        try:
            logger.debug(f"Processing message for session {session_id}: {message}")
            
            # Get or create chat history for the session
            if session_id not in chat_sessions:
                logger.debug(f"Creating new chat history for session {session_id}")
                chat_sessions[session_id] = ChatMessageHistory()

            # Create chain with history
            chain_with_history = RunnableWithMessageHistory(
                self.chain,
                lambda sid: chat_sessions[sid],
                input_messages_key="input",
                history_messages_key="history"
            )

            logger.debug("Sending request to LLM")
            response = await chain_with_history.ainvoke(
                {"input": message},
                config={
                    "configurable": {"session_id": session_id},
                    "metadata": {
                        "session_id": session_id,
                        **(metadata or {})
                    }
                }
            )
            logger.debug(f"Received response from LLM: {response.content}")
            return response.content

        except Exception as e:
            logger.error(f"Error in get_response: {str(e)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=500, detail=f"Chat processing error: {str(e)}")

class ChatbotBackend:
    def __init__(self, api_port: int = 8000):
        """Initialize the chatbot backend"""
        self.api_port = api_port
        self.response_queue = Queue()
        self.server_thread = None
        self.chat_sessions: Dict[str, ChatMessageHistory] = {}
        self.app = self._create_app()
        
        # Validate environment variables early
        self._validate_environment()

    def _validate_environment(self):
        """Validate required environment variables"""
        required_vars = {
            "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY"),
            "LANGCHAIN_API_KEY": os.getenv("LANGCHAIN_API_KEY")
        }
        
        missing_vars = [key for key, value in required_vars.items() if not value]
        if missing_vars:
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    def _create_app(self) -> FastAPI:
        """Create and configure FastAPI application"""
        app = FastAPI(
            title="Voice Chatbot API",
            description="API for voice-enabled chatbot with LangSmith tracing",
            version="1.0.0",
            lifespan=self._lifespan
        )

        # Add CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Add routes
        @app.post("/chat", response_model=ChatResponse)
        @traceable(run_type="chain")
        async def chat(request: ChatRequest, background_tasks: BackgroundTasks):
            try:
                logger.info(f"Received chat request for session {request.session_id}")
                response = await app.state.chatbot.get_response(
                    message=request.message,
                    session_id=request.session_id,
                    chat_sessions=self.chat_sessions,
                    metadata=request.metadata
                )
                # Put response in queue for main thread
                self.response_queue.put((request.session_id, response))
                return ChatResponse(response=response, session_id=request.session_id)
            except Exception as e:
                logger.error(f"Error in chat endpoint: {str(e)}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @app.delete("/chat/{session_id}")
        @traceable(run_type="chain")
        async def clear_chat_history(session_id: str):
            try:
                if session_id in self.chat_sessions:
                    self.chat_sessions[session_id].clear()
                    logger.info(f"Cleared chat history for session {session_id}")
                    return {"message": f"Chat history cleared for session {session_id}"}
                raise HTTPException(status_code=404, detail="Session not found")
            except Exception as e:
                logger.error(f"Error clearing chat history: {str(e)}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/health")
        async def health_check():
            return {"status": "healthy"}

        return app

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        """FastAPI lifecycle management"""
        try:
            logger.info("Initializing LLM components...")
            app.state.chatbot = await self._initialize_llm()
            logger.info("LLM components initialized successfully")
            yield
        except Exception as e:
            logger.error(f"Error in lifespan management: {str(e)}")
            logger.error(traceback.format_exc())
            raise
        finally:
            logger.info("Cleaning up resources...")
            self.chat_sessions.clear()

    @traceable(run_type="llm_init")
    async def _initialize_llm(self) -> ChatbotState:
        """Initialize the language model and related components"""
        try:
            # Initialize LLM
            llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=0.7,
                google_api_key=os.getenv("GOOGLE_API_KEY")
            )
            
            # Create prompt template
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a helpful AI assistant. Respond concisely and naturally."),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}")
            ])
            
            # Create chain
            chain = prompt | llm
            
            logger.info("LLM initialization completed successfully")
            return ChatbotState(llm, chain)
            
        except Exception as e:
            logger.error(f"Error initializing LLM: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    def start(self):
        """Start the backend server in a separate thread"""
        def run_server():
            try:
                logger.info(f"Starting backend server on port {self.api_port}")
                asyncio.run(self._run_server())
            except Exception as e:
                logger.error(f"Server error: {str(e)}")
                logger.error(traceback.format_exc())
                raise

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        logger.info(f"Backend server started on port {self.api_port}")

    async def _run_server(self):
        """Run the FastAPI server"""
        config = uvicorn.Config(
            self.app,
            host="0.0.0.0",
            port=self.api_port,
            log_level="debug"
        )
        server = uvicorn.Server(config)
        await server.serve()

    def stop(self):
        """Stop the backend server"""
        if self.server_thread:
            self.server_thread = None
            logger.info("Backend server stopped")

    def get_next_response(self) -> Optional[tuple[str, str]]:
        """Get the next response from the queue"""
        try:
            return self.response_queue.get_nowait()
        except:
            return None