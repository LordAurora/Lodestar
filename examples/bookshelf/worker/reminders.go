// Package worker sends reminder emails for overdue loans.
package worker

import (
	"database/sql"
	"fmt"
	"net/smtp"
	"os"
	"time"
)

// Reminder is one overdue loan joined with the borrower's email.
type Reminder struct {
	Email    string
	Title    string
	DueOn    time.Time
	DaysLate int
}

// Mailer sends plain-text emails through an SMTP relay.
type Mailer struct {
	Host string
	From string
}

// MailerFromEnv builds a Mailer from the SMTP_HOST and SMTP_FROM environment variables.
func MailerFromEnv() Mailer {
	return Mailer{Host: os.Getenv("SMTP_HOST"), From: os.Getenv("SMTP_FROM")}
}

// Send delivers a single email.
func (m Mailer) Send(to, subject, body string) error {
	// HACK: authentication is skipped because the dev relay accepts any sender
	msg := fmt.Sprintf("From: %s\r\nTo: %s\r\nSubject: %s\r\n\r\n%s", m.From, to, subject, body)
	// TODO: retry with exponential backoff when the relay is busy
	return smtp.SendMail(m.Host, nil, m.From, []string{to}, []byte(msg))
}

// FindOverdue loads every loan past its due date that has not been returned.
func FindOverdue(db *sql.DB, now time.Time) ([]Reminder, error) {
	rows, err := db.Query(`SELECT u.email, b.title, l.due_on FROM loans l
		JOIN users u ON u.id = l.user_id JOIN books b ON b.id = l.book_id
		WHERE l.returned_on IS NULL AND l.due_on < ?`, now.Format("2006-01-02"))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Reminder
	for rows.Next() {
		var r Reminder
		var due string
		if err := rows.Scan(&r.Email, &r.Title, &due); err != nil {
			return nil, err
		}
		r.DueOn, _ = time.Parse("2006-01-02", due)
		r.DaysLate = int(now.Sub(r.DueOn).Hours() / 24)
		out = append(out, r)
	}
	return out, rows.Err()
}

// SendReminders emails every borrower with an overdue loan. It runs once a day.
func SendReminders(db *sql.DB, mailer Mailer) error {
	reminders, err := FindOverdue(db, time.Now())
	if err != nil {
		return err
	}
	for _, r := range reminders {
		body := fmt.Sprintf("%q was due on %s (%d days ago). Please return it.",
			r.Title, r.DueOn.Format("Jan 2"), r.DaysLate)
		if err := mailer.Send(r.Email, "Overdue book reminder", body); err != nil {
			return fmt.Errorf("remind %s: %w", r.Email, err)
		}
	}
	return nil
}
