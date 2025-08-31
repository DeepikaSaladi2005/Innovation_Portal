# install requirements
pip install -r requirements.txt

# Database Setup
CREATE DATABASE innovation_portal;
CREATE TABLE departments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL
);

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    email VARCHAR(255),
    department_id INT,
    scholar_link TEXT,
    FOREIGN KEY (department_id) REFERENCES departments(id)
);

CREATE TABLE publications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    title TEXT,
    authors TEXT,
    year VARCHAR(10),
    citations INT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE form_fields (
    id INT AUTO_INCREMENT PRIMARY KEY,
    form_type VARCHAR(50), -- 'patent', 'publication', 'commercialization'
    field_label VARCHAR(100),
    field_name VARCHAR(100),
    field_type VARCHAR(20), -- text, number, date, textarea, select, file
    is_required BOOLEAN DEFAULT FALSE,
    options TEXT -- for dropdowns (comma-separated)
);
CREATE TABLE patents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT,
  title VARCHAR(255),
  inventors TEXT,
  patent_number VARCHAR(100),
  status VARCHAR(100),
  filed_date DATE,
  grant_date DATE,
  applicant TEXT,
  tech_transferred VARCHAR(10),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Adding extra fields
ALTER TABLE patents ADD COLUMN name TEXT;
ALTER TABLE patents ADD COLUMN email TEXT;
ALTER TABLE patents ADD COLUMN phone TEXT;
CREATE TABLE users_new (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255),
    email VARCHAR(255),
    department_id INT,
    scholar_link TEXT,
    password VARCHAR(255),
    role VARCHAR(20),
    FOREIGN KEY (department_id) REFERENCES departments(id)
);
CREATE TABLE commercializations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT,
  project_name VARCHAR(255),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

DESC commercializations;



# update config.py 
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "your_mysql_password",
    "database": "innovation_portal"
}

# Run the app
python app.py

