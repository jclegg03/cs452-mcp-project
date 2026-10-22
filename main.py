import os
from dotenv import load_dotenv

load_dotenv()

MODEL_API_KEY = os.getenv("MODEL_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

