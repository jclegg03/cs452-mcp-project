abstract class ModelConnector:
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    abstract def generate_response(self, prompt: str) -> str: