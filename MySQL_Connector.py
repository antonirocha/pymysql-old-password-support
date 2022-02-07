import mysql.connector
from mysql.connector import errorcode
import os
import dotenv

dotenv.load_dotenv(dotenv.find_dotenv())


con = mysql.connector.connect(
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASS"),
    host=os.getenv("DB_HOST"),
    port=os.getenv("DB_PORT"),
    database=os.getenv("DB_NAME"))

# con.close();

if con.is_connected():
    db_info = con.get_server_info()
    print("Connectado ao Servidor MySQL versão ",db_info)

    cursor = con.cursor()
    cursor.execute("SELECT database();")
    line = cursor.fetchone()
    print("Connectado ao Banco de Dados ",line)

if con.is_connected():
    cursor.close()
    con.close()
    print("Conexão com o Servidor MySQL foi encerrada...")