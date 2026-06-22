import uvicorn

if __name__ == "__main__":
    print("Jarvis 360° Dashboard — http://localhost:8000")
    print("API docs          — http://localhost:8000/docs")
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000, reload=True)
