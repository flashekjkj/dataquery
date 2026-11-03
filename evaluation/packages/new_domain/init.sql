CREATE TABLE jobs(id INTEGER PRIMARY KEY,title VARCHAR);
    CREATE TABLE applications(id INTEGER PRIMARY KEY, job_id INTEGER, stage VARCHAR);
    INSERT INTO jobs VALUES(1,'Engineer'),(2,'Analyst');
    INSERT INTO applications VALUES(1,1,'offer'),(2,1,'interview'),(3,2,'offer');