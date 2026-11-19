# MediConnect

> A full-stack healthcare platform for discovering doctors, booking clinic and video consultations, managing appointments, and supporting secure healthcare workflows.

MediConnect is a modern healthcare web application designed to connect **patients, doctors, and administrators** through a single platform.

Patients can discover doctors and book appointments, doctors can manage their practice and appointments, and administrators can review doctor applications and oversee platform activity.

The application combines a **React + Vite frontend**, **FastAPI backend**, and **MongoDB** database, with integrations for payments, notifications, OTP verification, AI-assisted prescription workflows, and video consultations.

---

## Table of Contents

- [Features](#features)
- [User Roles](#user-roles)
- [Technology Stack](#technology-stack)
- [Project Architecture](#project-architecture)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Backend](#running-the-backend)
- [Running the Frontend](#running-the-frontend)
- [Testing](#testing)
- [Production Build](#production-build)
- [Deployment](#deployment)
- [API Documentation](#api-documentation)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Future Improvements](#future-improvements)

---

## Features

### Patient

- Create an account and sign in
- Verify contact information using OTP
- Browse available doctors
- View doctor profiles and specializations
- Find available appointment slots
- Book clinic appointments
- Book video consultations
- Manage upcoming appointments
- View appointment history
- Receive appointment-related notifications
- Complete online payments through Razorpay
- Access healthcare-related documents and prescriptions

### Doctor

- Manage doctor profile and professional information
- Manage practice availability
- Create and manage appointment slots
- View scheduled appointments
- Manage patient consultations
- Support clinic and online consultations
- Generate and manage prescriptions
- Receive appointment notifications

### Administrator

- Review doctor applications
- Manage doctor approval workflows
- Monitor appointments
- Review platform activity
- Manage administrative workflows

### Platform

- MongoDB-backed data management
- OTP-based verification
- Email notifications
- SMS notifications
- Online payments through Razorpay
- Video consultation support
- AI-assisted prescription functionality
- REST API architecture
- Automated backend testing
- Production deployment configuration

---

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 |
| Frontend Tooling | Vite 6 |
| Language | TypeScript |
| Routing | React Router |
| UI Icons | Lucide React |
| Backend | FastAPI |
| Backend Language | Python |
| Database | MongoDB |
| Testing | Pytest + Mongomock |
| Payments | Razorpay |
| Email | Resend |
| SMS | Twilio |
| AI | Google Gemini |
| Deployment | Docker + Render |

The frontend package currently uses React 19, React Router 7, TypeScript 5.7 and Vite 6.

The backend development requirements include Pytest and Mongomock in addition to the main backend dependencies.

---

## Project Architecture

```text
                         ┌─────────────────────┐
                         │      Patients       │
                         └──────────┬──────────┘
                                    │
                                    │
                         ┌──────────▼──────────┐
                         │   React Frontend    │
                         │   React + Vite      │
                         │   TypeScript        │
                         └──────────┬──────────┘
                                    │
                              REST API
                                    │
                         ┌──────────▼──────────┐
                         │   FastAPI Backend   │
                         │                     │
                         │ Authentication      │
                         │ Doctors             │
                         │ Appointments        │
                         │ Payments            │
                         │ Notifications       │
                         │ Prescriptions       │
                         └──────────┬──────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
       ┌──────▼──────┐       ┌──────▼──────┐       ┌─────▼──────┐
       │   MongoDB   │       │  External   │       │    AI      │
       │   Database  │       │  Services   │       │  Gemini    │
       └─────────────┘       └─────────────┘       └────────────┘
                                  │
                     ┌────────────┼────────────┐
                     │            │            │
                 Razorpay      Resend       Twilio
```

---

## Project Structure

```text
MediConnect/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── services/
│   │   ├── ...
│   │   └── main.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── seed.py
│   └── manage_database.py
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── ...
│   │   └── App.tsx
│   │
│   ├── package.json
│   └── vite.config.*
│
├── docs/
│   └── ...
│
├── .dockerignore
├── .gitignore
├── Dockerfile
├── render.yaml
├── DEMO_ACCOUNTS.md
└── README.md
```

The repository separates the application into `frontend/` and `backend/`, with backend tests and database utilities maintained alongside the API implementation.

---

# Getting Started

## Prerequisites

Install the following before starting:

- **Python 3.13**
- **Node.js 22 LTS or newer**
- **pnpm 11**
- MongoDB database
- Git

You will also need credentials for the external services used by the features you want to enable.

---

## 1. Clone the Repository

```bash
git clone https://github.com/harsh001-debug/MediConnect.git
cd MediConnect
```

Install the required pnpm version:

```bash
npm install --global pnpm@11.19.0
```

If pnpm 11 is already installed, this step can be skipped.

---

# Environment Variables

MediConnect uses environment variables for database credentials, authentication secrets, payment providers, notifications, and AI services.

**Never commit real credentials or API keys to GitHub.**

Create your local environment file based on the project's environment-variable template.

For example:

```text
backend/
└── .env
```

A typical configuration may include:

```env
MONGODB_URI=
MONGODB_DB_NAME=mediconnect

RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=

OTP_SECRET=

OTP_EMAIL_FROM=
RESEND_API_KEY=

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_MESSAGING_SERVICE_SID=

APP_ENV=development

PRESCRIPTION_AI_PROVIDER=gemini
GEMINI_API_KEY=
GEMINI_MODEL=
```

Use your own credentials for each service.

The repository's Render configuration also keeps sensitive values outside source control by requiring them as deployment environment variables.

> **Security:** Do not upload `.env` files containing real secrets to GitHub.

---

# Running the Backend

Open a terminal at the project root:

```bash
cd backend
```

Create a Python virtual environment.

### Windows PowerShell

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
python -m pip install -r requirements-dev.txt
```

Start FastAPI:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

The API will be available at:

```text
http://localhost:8000
```

Health check:

```text
http://localhost:8000/api/v1/health
```

FastAPI's interactive API documentation is available at:

```text
http://localhost:8000/docs
```

---

# Running the Frontend

Open a second terminal from the project root:

```bash
cd frontend
```

Install dependencies:

```bash
pnpm install
```

Start the development server:

```bash
pnpm dev
```

The frontend will normally be available at:

```text
http://localhost:5173
```

The frontend is built with React, TypeScript, Vite and React Router.

---

# Testing

Backend tests are located in:

```text
backend/tests/
```

From the `backend` directory:

### Windows

```powershell
python -m pytest -q
```

### macOS / Linux

```bash
python -m pytest -q
```

The development test configuration uses Pytest and Mongomock for isolated database-related testing.

---

# Production Build

To create a production frontend build:

```bash
cd frontend
pnpm build
```

The build performs TypeScript checking and generates the Vite production bundle.

To preview the production build locally:

```bash
pnpm preview
```

---

# Deployment

MediConnect includes a `render.yaml` configuration for deployment on **Render**.

The deployment configuration defines:

- Docker-based web service
- MongoDB connection
- Razorpay configuration
- OTP configuration
- Email configuration
- Twilio SMS configuration
- Gemini AI configuration
- Production environment settings
- API health-check endpoint

The configured Render service uses:

```text
/api/v1/health
```

as its health-check path.

### Deployment Environment Variables

Production secrets should be configured through the hosting provider's environment-variable system.

Do not place production credentials directly inside:

- Source code
- `README.md`
- `render.yaml`
- Frontend JavaScript/TypeScript files
- Git commits

---

# API Documentation

When the backend is running locally, FastAPI provides interactive API documentation at:

```text
http://localhost:8000/docs
```

The API is organized around areas such as:

```text
Authentication
Doctors
Appointments
Patients
Guests
Administration
Payments
Notifications
Prescriptions
```

---

# Security

MediConnect handles healthcare-related information and integrates with external services, so security should be treated as a core requirement.

### Recommended Practices

- Never commit `.env` files.
- Never expose API keys in frontend source code.
- Use environment variables for secrets.
- Rotate credentials if they are accidentally exposed.
- Restrict MongoDB network access where possible.
- Use HTTPS in production.
- Use test/sandbox payment credentials during development.
- Avoid using real patient information during development and testing.
- Keep dependencies updated.
- Apply appropriate authorization checks to protected API endpoints.

### Important

MediConnect is a software project and should not be considered a substitute for a certified clinical system, medical advice, or regulatory compliance assessment.

Before using the platform with real patient data, conduct appropriate security, privacy, compliance, infrastructure, and clinical-safety reviews.

---

# Troubleshooting

## Backend health check fails

Check:

1. MongoDB connection string
2. MongoDB database name
3. MongoDB network access rules
4. Database credentials
5. Environment variables
6. FastAPI terminal for startup errors

---

## Doctor directory is empty

Make sure the database contains approved/demo doctors.

The repository includes database/seed utilities that can be used to prepare development data.

---

## OTP email or SMS is not received

Check:

- Email/SMS provider credentials
- Recipient contact information
- Provider delivery logs
- Environment variables
- Backend startup logs

---

## Razorpay checkout is unavailable

Verify:

```text
RAZORPAY_KEY_ID
RAZORPAY_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET
```

Use Razorpay Test Mode credentials during development.

---

## Frontend cannot communicate with backend

Make sure FastAPI is running on:

```text
http://localhost:8000
```

Then restart the Vite development server if the API configuration was changed.

---

# Development Workflow

A typical development workflow is:

```text
1. Clone repository
       ↓
2. Configure environment variables
       ↓
3. Start MongoDB
       ↓
4. Start FastAPI backend
       ↓
5. Start React frontend
       ↓
6. Test authentication
       ↓
7. Test doctor discovery
       ↓
8. Test appointment booking
       ↓
9. Test payments / notifications
       ↓
10. Run automated tests
       ↓
11. Build frontend
       ↓
12. Deploy
```

---

# Future Improvements

Potential areas for further development include:

- Enhanced role-based access control
- More comprehensive automated test coverage
- Improved appointment scheduling workflows
- Advanced doctor search and filtering
- Better notification management
- Enhanced video consultation experience
- Healthcare document management
- Audit logging
- Advanced administrative analytics
- Improved observability and monitoring
- Production-grade security and compliance hardening
- CI/CD automation

---

# Contributing

Contributions are welcome.

A typical contribution workflow:

```bash
git checkout -b feature/your-feature
```

Make your changes, test them locally, and commit:

```bash
git add .
git commit -m "Add: your feature"
```

Push the branch:

```bash
git push origin feature/your-feature
```

Then open a Pull Request.

Before submitting a contribution:

- Ensure tests pass.
- Ensure the frontend builds successfully.
- Do not commit secrets.
- Keep changes focused.
- Update documentation when necessary.

---

# License

Add the project's chosen license here before distributing the project publicly.

---

## Project Status

MediConnect is an actively developed full-stack healthcare application.

The project is suitable for **development, demonstration, testing, and further extension**. Production use with real healthcare data requires additional security, privacy, regulatory, infrastructure, and clinical-safety validation.

---

## Author

**Harsh Kumar Tiwari**

GitHub: [@harsh001-debug](https://github.com/harsh001-debug)

Repository: [MediConnect](https://github.com/harsh001-debug/MediConnect)