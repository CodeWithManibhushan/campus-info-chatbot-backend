import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

def get_connection():
    """Connect to MySQL database (Aiven / Cloud compatible)"""
    try:
        connection = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST"),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            database=os.getenv("MYSQL_DATABASE"),
            port=int(os.getenv("MYSQL_PORT", 3306)),  # ✅ PORT added
            ssl_disabled=False                         # ✅ SSL enabled (Aiven required)
        )
        return connection
    except Error as e:
        print("❌ Error connecting to MySQL:", e)
        return None


def run_query(query, params=None, fetch=False):
    """Execute SQL query safely."""
    connection = get_connection()
    if not connection:
        return None

    try:
        cursor = connection.cursor(dictionary=True, buffered=True)
        cursor.execute(query, params or ())

        result = None
        if fetch:
            result = cursor.fetchall()

        connection.commit()
        return result

    except Error as e:
        print("❌ Query execution failed:", e)
        return None

    finally:
        try:
            if cursor:
                cursor.close()
            if connection.is_connected():
                connection.close()
        except:
            pass
