import os
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()
db_url = os.environ.get("DATABASE_URL")

if not db_url:
    print("Error: DATABASE_URL not found in .env")
    exit(1)

print("Attempting to connect to AWS RDS...")
try:
    # Add a connect_timeout so we don't hang if it's a firewall issue
    engine = create_engine(db_url, connect_args={'connect_timeout': 5})
    
    with engine.connect() as conn:
        print("✅ Successfully connected to AWS PostgreSQL Database!")
        
    print("Uploading cpd_courses.csv to 'courses' table...")
    if os.path.exists("cpd_courses.csv"):
        df_courses = pd.read_csv("cpd_courses.csv")
        df_courses.to_sql('courses', engine, if_exists='replace', index=False)
        print(f"✅ Created/Updated 'courses' table with {len(df_courses)} records.")
    else:
        print("⚠️ cpd_courses.csv not found, skipping courses table.")

    print("Uploading cpd_feedback.csv to 'feedback' table...")
    if os.path.exists("cpd_feedback.csv"):
        df_feedback = pd.read_csv("cpd_feedback.csv")
        df_feedback.to_sql('feedback', engine, if_exists='replace', index=False)
        print(f"✅ Created/Updated 'feedback' table with {len(df_feedback)} records.")
    else:
        # Create an empty feedback table
        df_feedback = pd.DataFrame(columns=["EventID", "Status", "ManualPrice", "Notes"])
        df_feedback.to_sql('feedback', engine, if_exists='replace', index=False)
        print("✅ Created empty 'feedback' table.")

    print("\n🎉 Database setup complete! Both tables exist on AWS RDS.")

except Exception as e:
    print("\n❌ DATABASE CONNECTION ERROR ❌")
    print("If it hung for a long time or timed out, this is an AWS Security Group (Firewall) issue.")
    print("By default AWS blocks all external connections from your laptop.")
    print("\nHow to fix it:")
    print("1. Go to AWS RDS Console -> Click your database instance")
    print("2. Under 'Connectivity & security', click the link under 'VPC security groups'")
    print("3. Edit Inbound Rules -> Add Rule")
    print("4. Type: PostgreSQL | Port: 5432 | Source: 0.0.0.0/0 (or your explicit IP address)")
    print("5. Save rule and run this script again.")
    print(f"\nExact Error Details:\n{e}")
