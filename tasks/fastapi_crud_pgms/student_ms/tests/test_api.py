"""API tests for the Student Management System. No MySQL required.

The real repository is swapped for an in-memory one via dependency
overrides. TestClient is used *without* a `with` block on purpose: that
skips the app lifespan, which would otherwise connect to MySQL to create
the table.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from student_ms.main import app
from student_ms.repository import get_repository
from student_ms.schemas import Student, StudentCreate, StudentUpdate


class InMemoryStudentRepository:
    """Same public methods as StudentRepository, backed by a dict."""

    def __init__(self) -> None:
        self._students: dict[int, Student] = {}
        self._next_id = 1

    def add(self, student: StudentCreate) -> Student:
        stored = Student(id=self._next_id, **student.model_dump())
        self._students[stored.id] = stored
        self._next_id += 1
        return stored

    def list_page(self, limit: int, offset: int) -> list[Student]:
        ordered = sorted(self._students.values(), key=lambda s: s.id)
        return ordered[offset : offset + limit]

    def count(self) -> int:
        return len(self._students)

    def get(self, student_id: int) -> Student | None:
        return self._students.get(student_id)

    def update(self, student_id: int, changes: StudentUpdate) -> Student | None:
        current = self._students.get(student_id)
        if current is None:
            return None
        updated = current.model_copy(update=changes.model_dump(exclude_none=True))
        self._students[student_id] = updated
        return updated

    def delete(self, student_id: int) -> bool:
        return self._students.pop(student_id, None) is not None

    def delete_all(self) -> int:
        deleted = len(self._students)
        self._students.clear()
        return deleted


@pytest.fixture
def client() -> Iterator[TestClient]:
    repository = InMemoryStudentRepository()
    app.dependency_overrides[get_repository] = lambda: repository
    yield TestClient(app)
    app.dependency_overrides.clear()


def make_student(client: TestClient, **overrides: object) -> dict:
    payload = {"name": "Asha", "age": 20, "course": "Physics", "marks": 88.5}
    payload.update(overrides)
    response = client.post("/students", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------- Web UI ----------

def test_root_serves_the_web_ui(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Student Management</title>" in response.text


# ---------- Create / read ----------

def test_add_student_returns_it_with_an_id(client: TestClient) -> None:
    created = make_student(client)

    assert created == {
        "id": 1, "name": "Asha", "age": 20, "course": "Physics", "marks": 88.5,
    }
    assert client.get("/students/1").json() == created


def test_add_student_strips_surrounding_whitespace(client: TestClient) -> None:
    created = make_student(client, name="  Ravi  ", course=" Maths ")

    assert (created["name"], created["course"]) == ("Ravi", "Maths")


@pytest.mark.parametrize(
    "overrides",
    [
        {"marks": -1},
        {"marks": 100.5},
        {"age": 0},
        {"name": "   "},
        {"course": ""},
        {"name": "x" * 101},
    ],
)
def test_add_student_rejects_invalid_fields(client: TestClient, overrides: dict) -> None:
    payload = {"name": "Asha", "age": 20, "course": "Physics", "marks": 88.5, **overrides}

    assert client.post("/students", json=payload).status_code == 422


def test_get_missing_student_returns_404(client: TestClient) -> None:
    assert client.get("/students/99").status_code == 404


# ---------- List / pagination ----------

def test_list_is_empty_initially(client: TestClient) -> None:
    assert client.get("/students").json() == {
        "items": [], "total": 0, "limit": 10, "offset": 0,
    }


def test_list_defaults_to_first_ten_sorted_by_id(client: TestClient) -> None:
    for i in range(12):
        make_student(client, name=f"Student {i}")

    page = client.get("/students").json()

    assert [s["id"] for s in page["items"]] == list(range(1, 11))
    assert (page["total"], page["limit"], page["offset"]) == (12, 10, 0)


def test_list_offset_returns_the_remainder(client: TestClient) -> None:
    for i in range(12):
        make_student(client, name=f"Student {i}")

    page = client.get("/students", params={"offset": 10}).json()

    assert [s["id"] for s in page["items"]] == [11, 12]
    assert page["total"] == 12


def test_list_respects_custom_limit(client: TestClient) -> None:
    for i in range(5):
        make_student(client, name=f"Student {i}")

    page = client.get("/students", params={"limit": 2, "offset": 1}).json()

    assert [s["id"] for s in page["items"]] == [2, 3]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_rejects_out_of_range_paging(client: TestClient, params: dict) -> None:
    assert client.get("/students", params=params).status_code == 422


# ---------- Update ----------

@pytest.mark.parametrize(
    ("changes", "expected_course", "expected_marks"),
    [
        ({"marks": 95}, "Physics", 95.0),
        ({"course": "Chemistry"}, "Chemistry", 88.5),
        ({"course": "Biology", "marks": 70}, "Biology", 70.0),
    ],
)
def test_update_changes_only_given_fields(
    client: TestClient, changes: dict, expected_course: str, expected_marks: float
) -> None:
    make_student(client)

    response = client.patch("/students/1", json=changes)

    assert response.status_code == 200
    updated = response.json()
    assert (updated["course"], updated["marks"]) == (expected_course, expected_marks)
    assert (updated["name"], updated["age"]) == ("Asha", 20)
    assert client.get("/students/1").json() == updated


def test_update_with_empty_body_returns_422(client: TestClient) -> None:
    make_student(client)

    assert client.patch("/students/1", json={}).status_code == 422


def test_update_rejects_out_of_range_marks(client: TestClient) -> None:
    make_student(client)

    assert client.patch("/students/1", json={"marks": 101}).status_code == 422


def test_update_missing_student_returns_404(client: TestClient) -> None:
    assert client.patch("/students/99", json={"marks": 50}).status_code == 404


# ---------- Delete ----------

def test_delete_student_removes_it(client: TestClient) -> None:
    make_student(client)

    response = client.delete("/students/1")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/students/1").status_code == 404


def test_delete_missing_student_returns_404(client: TestClient) -> None:
    assert client.delete("/students/99").status_code == 404


def test_delete_all_requires_confirmation(client: TestClient) -> None:
    make_student(client)

    assert client.delete("/students").status_code == 400
    assert client.get("/students").json()["total"] == 1


def test_delete_all_with_confirmation_empties_the_table(client: TestClient) -> None:
    make_student(client)
    make_student(client, name="Ravi")

    response = client.delete("/students", params={"confirm": "true"})

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert client.get("/students").json()["total"] == 0
