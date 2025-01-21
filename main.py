# main.py
import os
import streamlit as st
import threading
import time
import speech_recognition as sr
import pyttsx3
import requests
from backend import ChatbotBackend
from langsmith import traceable
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class IntegratedVoiceChatbot:
    def __init__(self):
        """Initialize the integrated voice chatbot system"""
        self.setup_streamlit()
        self.setup_voice_components()
        self.setup_backend()
        self.setup_response_thread()

    def setup_streamlit(self):
        """Initialize Streamlit UI components and session state"""
        if 'messages' not in st.session_state:
            st.session_state.messages = []
        if 'audio_mode' not in st.session_state:
            st.session_state.audio_mode = False
        if 'system_ready' not in st.session_state:
            st.session_state.system_ready = False

    @traceable(run_type="setup")
    def setup_voice_components(self):
        """Initialize speech recognition and TTS components"""
        try:
            # Initialize speech recognition
            self.recognizer = sr.Recognizer()
            
            # Initialize text-to-speech engine
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 150)
            self.tts_engine.setProperty('volume', 0.9)
            
            # Session ID for this instance
            self.session_id = "default"
            
            logger.info("Voice components initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize voice components: {str(e)}")
            raise

    @traceable(run_type="setup")
    def setup_backend(self):
        """Initialize and start the backend server"""
        try:
            self.backend = ChatbotBackend(api_port=8000)
            self.backend.start()
            time.sleep(2)  # Give the backend server time to start
            st.session_state.system_ready = True
            logger.info("Backend server started successfully")
        except Exception as e:
            logger.error(f"Failed to start backend server: {str(e)}")
            st.error(f"Failed to start backend server: {str(e)}")
            st.stop()

    def setup_response_thread(self):
        """Setup thread to check for responses from backend"""
        def check_responses():
            while True:
                response = self.backend.get_next_response()
                if response:
                    session_id, message = response
                    if message:
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": message
                        })
                time.sleep(0.1)

        self.response_thread = threading.Thread(target=check_responses, daemon=True)
        self.response_thread.start()
        logger.info("Response thread started")

    @traceable(run_type="speech_to_text")
    def listen(self) -> str:
        """Listen for voice input and convert it to text"""
        with sr.Microphone() as source:
            logger.info("Listening for voice input...")
            self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=15)
                logger.info("Processing speech...")
                text = self.recognizer.recognize_google(audio)
                logger.info(f"Recognized text: {text}")
                return text
            except sr.WaitTimeoutError:
                logger.warning("No speech detected")
                return ""
            except sr.UnknownValueError:
                logger.warning("Could not understand audio")
                return ""
            except sr.RequestError as e:
                logger.error(f"Speech recognition error: {e}")
                return ""

    @traceable(run_type="text_to_speech")
    def speak(self, text: str):
        """Convert text to speech and play it"""
        try:
            self.tts_engine.say(text)
            self.tts_engine.runAndWait()
            logger.info("Text-to-speech completed successfully")
        except Exception as e:
            logger.error(f"TTS Error: {e}")

    @traceable(run_type="chat")
    def chat(self, message: str) -> str:
        """Send message to backend and get response"""
        try:
            response = requests.post(
                f"http://localhost:8000/chat",
                json={
                    "message": message,
 "session_id": self.session_id,
                    "metadata": {"input_type": "voice" if st.session_state.audio_mode else "text"}
                }
            )
            response.raise_for_status()
            return response.json()["response"]
        except Exception as e:
            logger.error(f"Chat error: {e}")
            return f"Error communicating with backend: {str(e)}"

    @traceable(run_type="voice_input")
    def handle_voice_input(self):
        """Handle voice input through VoiceChatbot"""
        st.info("🎤 Listening... (Speak now)")
        try:
            text = self.listen()
            if text:
                st.session_state.messages.append({
                    "role": "user",
                    "content": text
                })
                
                response = self.chat(text)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response
                })
                if response and st.session_state.audio_mode:
                    self.speak(response)
                return True
        except Exception as e:
            logger.error(f"Voice input error: {e}")
            st.error(f"Error processing voice input: {str(e)}")
        return False

    @traceable(run_type="text_input")
    def handle_text_input(self, text: str):
        """Handle text input through both systems"""
        try:
            st.session_state.messages.append({
                "role": "user",
                "content": text
            })
            
            response = self.chat(text)
            st.session_state.messages.append({
                "role": "assistant",
                "content": response
            })
            if response and st.session_state.audio_mode:
                self.speak(response)
                
        except Exception as e:
            logger.error(f"Text input error: {e}")
            st.error(f"Error processing text input: {str(e)}")

    @traceable(run_type="clear")
    def clear_history(self):
        """Clear chat history in both systems"""
        try:
            requests.delete(f"http://localhost:8000/chat/{self.session_id}")
            st.session_state.messages = []
            logger.info("Chat history cleared successfully")
        except Exception as e:
            logger.error(f"Error clearing history: {e}")
            st.error(f"Error clearing history: {str(e)}")

    @traceable(run_type="render")
    def render(self):
        """Render the Streamlit UI"""
        st.title("🎙️ Chatbot")
        
        # System status indicator
        if st.session_state.system_ready:
            st.success("System ready!")
        else:
            st.warning("System initializing...")
            return

        # Sidebar controls
        with st.sidebar:
            st.header("Settings")
            
            # Input mode toggle
            st.session_state.audio_mode = st.toggle(
                "Voice Input Mode",
                value=st.session_state.audio_mode
            )
            
            # Clear history button
            if st.button("Clear History", type="secondary"):
                self.clear_history()

            # Display commands help
            st.markdown("""
            ### Available Commands:
            - Say or type 'quit' to exit
            - Say or type 'clear' to clear history
            - Say or type 'type' to use keyboard
            - Say or type 'voice' to use voice
            """)

        # Chat messages display
        chat_container = st.container()
        with chat_container:
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.write(message["content"])

        # Input section
        if st.session_state.system_ready:
            if st.session_state.audio_mode:
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Start Recording", type="primary"):
                        if self.handle_voice_input():
                            st.rerun()
            else:
                if prompt := st.chat_input("Type your message..."):
                    if prompt.lower() in ['quit', 'clear', 'type', 'voice']:
                        if prompt.lower() == 'quit':
                            st.stop()
                        elif prompt.lower() == 'clear':
                            self.clear_history()
                        elif prompt.lower() == 'type':
                            st.session_state.audio_mode = False
                        elif prompt.lower() == 'voice':
                            st.session_state.audio_mode = True
                    else:
                        self.handle_text_input(prompt)
                    st.rerun()

def check_environment():
    """Check required environment variables"""
    required_vars = ["GOOGLE_API_KEY", "LANGCHAIN_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        st.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        st.markdown("""
        Please set the following environment variables:
        ```bash
        export GOOGLE_API_KEY=your_google_api_key
        export LANGCHAIN_API_KEY=your_langchain_api_key
        ```
        """)
        return False
    return True

def main():
    """Main function to run the integrated chatbot system"""
    if check_environment():
        system = IntegratedVoiceChatbot()
        system.render()

if __name__ == "__main__":
    main()