import sys
import requests
import psycopg2

def main():
    try:
        # Fetch contacts from API
        print("Fetching contacts from Node API...")
        res = requests.get('http://localhost:3000/api/fix_lids', timeout=120)
        data = res.json()
        if data.get('status') != 'success':
            print("API error:", data.get('message'))
            return
            
        contacts = data.get('contacts', [])
        print(f"Loaded {len(contacts)} C.US contacts from WhatsApp.")
        
        # Connect to DB
        conn = psycopg2.connect("dbname=Wood user=odoo")
        cur = conn.cursor()
        
        # Get LID sessions
        cur.execute("SELECT id, mobile, whatsapp_name FROM whatsapp_session WHERE LENGTH(mobile) >= 14 AND mobile NOT LIKE '+%';")
        sessions = cur.fetchall()
        print(f"Found {len(sessions)} LID sessions in DB.")
        
        updates_made = 0
        
        for sid, mobile, name in sessions:
            if not name:
                continue
            
            # Find match in contacts
            match = next((c for c in contacts if c.get('name') == name), None)
            if match and match.get('number'):
                real_number = match['number']
                print(f"Fixing session {sid}: '{name}' -> from {mobile} to {real_number}")
                
                # Update DB
                try:
                    # Use a savepoint to catch unique constraint violation
                    cur.execute("SAVEPOINT sp1")
                    cur.execute("UPDATE whatsapp_session SET mobile = %s WHERE id = %s", (real_number, sid))
                    cur.execute("RELEASE SAVEPOINT sp1")
                    updates_made += 1
                except psycopg2.errors.UniqueViolation:
                    cur.execute("ROLLBACK TO SAVEPOINT sp1")
                    print(f"Skipping {sid} due to unique constraint violation.")
                
        conn.commit()
        cur.close()
        conn.close()
        print(f"Successfully updated {updates_made} sessions!")
        
    except Exception as e:
        print("Error:", e)

if __name__ == '__main__':
    main()
