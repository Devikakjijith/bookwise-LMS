use library_mng_system;

SELECT t.id, b.title, u.name AS member, t.issue_date, t.due_date, t.status
FROM transactions t
JOIN books b ON b.id = t.book_id
JOIN users u ON u.id = t.member_id
WHERE t.status = 'issued';

UPDATE transactions
SET due_date = DATE_SUB(CURDATE(), INTERVAL 5 DAY)
WHERE id =4;