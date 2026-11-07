from fastapi import FastAPI
from app.api import auth, orders
from app.utils import legacy

app = FastAPI(title="Shop")
app.include_router(auth.router)
app.include_router(orders.router)


@app.get("/report/{name}")
def report(name: str):
    return legacy.run_report(name)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app)
