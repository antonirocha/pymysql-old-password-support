
import pymysql.cursors
import os, dotenv
import time

dotenv.load_dotenv(dotenv.find_dotenv())

connection = pymysql.connect(
    host = os.getenv("DB_HOST"),
    port = int(os.getenv("DB_PORT")),
    user = os.getenv("DB_USER"),
    database = os.getenv("DB_NAME"),
    password = os.getenv("DB_PASS"),
    cursorclass = pymysql.cursors.DictCursor)

with connection.cursor() as cursor:

    db = "SELECT database();"
    cursor.execute(db)
    resultdb = cursor.fetchone()

    sql = "SELECT COD_LOJA, SIGLA FROM LOJA WHERE COD_LOJA = @@SERVER_ID;"
    cursor.execute(sql)
    result = cursor.fetchone()
    store = result['SIGLA']
    connection.commit()


    print("\nConectado ao Banco de Dados:",resultdb['database()'])
    print("Servidor:",os.getenv("DB_HOST"),":",os.getenv("DB_PORT"))
    print("Usuário:",os.getenv("DB_USER"))
    print("Password: ************")
    print("Loja:",store,"\n")
    time.sleep(2)

    connection.connect():
    connection.close()
    print("\nA conexão com o Servidor e Banco de Dados",resultdb['database()'],"foi encerrada.\n")