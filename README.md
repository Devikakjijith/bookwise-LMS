📚 Bookwise — Library Management System

A small, practical library management web app built with Flask and MySQL — add books, register members, issue and return copies, and track fines, all from one dashboard. Built as an academic project to demonstrate full-stack web development with server-side rendering, session-based authentication, and relational database design.

✨ Features
Two user roles — Admin (librarian) and Member, each with their own views and permissions
Authentication — member registration with password strength rules, separate member/admin login, logout
Book catalog — searchable by title, author, or category, with live availability
Admin book management — add, edit, and delete books, with safeguards against duplicates and inconsistent copy counts
Issue & Return workflow — 14-day borrowing period, automatic due date calculation, and a ₹5/day late fine calculated automatically on return
Member dashboard — currently borrowed books, due dates, live fine estimates, and borrowing history
Admin dashboard — library-wide stats (total books, members, issued count, overdue count) and a "who owes a fine" report
Zero manual database setup — tables and a default admin account are created automatically on first run
Flash messages for every action (success, error, and warning states), auto-dismissing after 10 seconds
Consistent UI — one base template (shared navbar, header, footer) and one shared stylesheet across every page
🛠 Tech Stack
Layer	Technology
Backend	Python, Flask
Database	MySQL
Templating	Jinja2
Frontend	HTML5, CSS3 (no framework — hand-written responsive layout)
Auth	Flask sessions + Werkzeug password hashing (scrypt)

*Note*- don't forget to update the app.py sql settings with your sql root password.
