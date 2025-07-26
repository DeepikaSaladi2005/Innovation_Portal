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


# update config.py 
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "your_mysql_password",
    "database": "innovation_portal"
}

# Run the app
python app.py


