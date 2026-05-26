import os
import uuid
import requests
from fastapi import HTTPException

# Variables de entorno (Deberás configurarlas en tu .env o ponerlas aquí temporalmente)
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "dev-bzem6wpwmlr14eha.us.auth0.com")
AUTH0_MTM_CLIENT_ID = os.getenv("AUTH0_MTM_CLIENT_ID", "Mj73xiHNsKsqCR9G6yGpVv26oTsBEqK1")
AUTH0_MTM_CLIENT_SECRET = os.getenv("AUTH0_MTM_CLIENT_SECRET", "unTrwJD8LftO4XB5SGRPRAEpCmaMfqwnMgGqxP2S63TPHnkV4XAywihLqNFUPm4n")

def get_management_token() -> str:
    """Obtiene el token de administrador para hablar con Auth0 Management API"""
    url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "client_id": AUTH0_MTM_CLIENT_ID,
        "client_secret": AUTH0_MTM_CLIENT_SECRET,
        "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
        "grant_type": "client_credentials"
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Error conectando con Auth0 Management API.")
    return response.json().get("access_token")

def crear_usuario_en_auth0(email: str) -> str:
    """Crea el usuario en Auth0 y dispara el correo de asignación de contraseña"""
    token = get_management_token()
    url = f"https://{AUTH0_DOMAIN}/api/v2/users"
    
    # Auth0 requiere una contraseña inicial. Le damos una hiper-segura y aleatoria que nadie conocerá.
    temp_password = f"Tymeo{uuid.uuid4()}!" 
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "email": email,
        "password": temp_password,
        "connection": "Username-Password-Authentication",
        "email_verified": False,
        "verify_email": False # Lo apagamos porque mandaremos el de reseteo de password
    }
    
    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 409:
        raise HTTPException(status_code=400, detail="Este correo ya tiene una cuenta.")
    if response.status_code != 201:
        raise HTTPException(status_code=500, detail=f"Error de Auth0: {response.text}")
        
    user_id = response.json().get("user_id")
    
    # Disparamos el ticket de "Olvidé mi contraseña" para que el usuario configure la suya
    trigger_password_reset(email)
    
    return user_id

def trigger_password_reset(email: str):
    """Obliga a Auth0 a enviar el mail de 'Cambia tu contraseña'"""
    url = f"https://{AUTH0_DOMAIN}/dbconnections/change_password"
    payload = {
        "client_id": AUTH0_MTM_CLIENT_ID, 
        "email": email,
        "connection": "Username-Password-Authentication"
    }
    requests.post(url, json=payload)