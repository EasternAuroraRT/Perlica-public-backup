from __future__ import annotations

from typing import * # pyright: ignore[reportWildcardImportFromLibrary]
from abc import * # pyright: ignore[reportWildcardImportFromLibrary]
import keyword


class Dimension:
    def __init__(self, name: str, default_value: float = 0) -> None:
        self.name: str = name
        self.default_value: float = default_value


class VectorMeta(type):
    def __new__(
        mcls,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        elements = namespace.get("elements")
        if elements is not None:
            if not elements:
                raise ValueError(f"{name}.elements must not be empty")
            taken = set(namespace)
            taken.update(("elements", "_element_names", "__slots__", "__init__"))
            for base in bases:
                taken.update(dir(base))
            slots: list[str] = []
            for element in elements:
                if not isinstance(element, Dimension):
                    raise TypeError(
                        f"{name}.elements must only contain {Dimension.__name__} instances"
                    )
                dim_name = element.name
                if (
                    not dim_name.isidentifier()
                    or dim_name.startswith("__")
                    or keyword.iskeyword(dim_name)
                ):
                    raise ValueError(
                        f"{name}: {Dimension.__name__} name {dim_name!r} is not a valid member name"
                    )
                if dim_name in slots:
                    raise ValueError(
                        f"{name}: duplicated {Dimension.__name__} name {dim_name!r}"
                    )
                if dim_name in taken:
                    raise ValueError(
                        f"{name}: {Dimension.__name__} name {dim_name!r} collides with an existing member"
                    )
                slots.append(dim_name)
                taken.add(dim_name)
            namespace["elements"] = tuple(elements)
            namespace["_element_names"] = tuple(slots)
            namespace["__slots__"] = tuple(slots)
            if "__init__" not in namespace:
                body = "".join(
                    f"    self.{element.name} = {element.default_value!r}\n"
                    for element in elements
                )
                init_namespace: dict[str, Any] = {}
                exec(
                    compile(
                        f"def __init__(self):\n{body}", f"<{name}.__init__>", "exec"
                    ),
                    init_namespace,
                )
                init = init_namespace["__init__"]
                init.__qualname__ = f"{name}.__init__"
                namespace["__init__"] = init
        return super().__new__(mcls, name, bases, namespace, **kwargs)


class Vector(metaclass=VectorMeta):
    __slots__ = ()

    _element_names: ClassVar[tuple[str, ...]]

    def __init__(self) -> None:
        elements = getattr(type(self), "elements", ())
        if not elements:
            raise NotImplementedError(
                "Vector is abstract: subclass it and declare a non-empty `elements` list"
            )
        for element in elements:
            setattr(self, element.name, element.default_value)

    def _new(self) -> Vector:
        return cast(Vector, object.__new__(type(self)))

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{name}={getattr(self, name)!r}" for name in self._element_names
        )
        return f"{type(self).__name__}({fields})"

    def _require_compatible(self, other: Any, op: str) -> Vector:
        if type(self) is not type(other):
            raise TypeError(
                f"unsupported operand type(s) for {op}: "
                f"{type(self).__name__} and {type(other).__name__}"
            )
        return cast(Vector, other)

    def _elementwise(
        self, other: Vector, op: str, fn: Callable[[float, float], float]
    ) -> Vector:
        other = self._require_compatible(other, op)
        result = self._new()
        for name in self._element_names:
            setattr(result, name, fn(getattr(self, name), getattr(other, name)))
        return result

    def _scaled(
        self, scalar: float, op: str, fn: Callable[[float, float], float]
    ) -> Vector:
        if not isinstance(scalar, (int, float)):
            raise TypeError(
                f"unsupported operand type(s) for {op}: "
                f"{type(self).__name__} and {type(scalar).__name__}"
            )
        result = self._new()
        for name in self._element_names:
            setattr(result, name, fn(getattr(self, name), scalar))
        return result

    def _transformed(self, fn: Callable[[float], float]) -> Vector:
        result = self._new()
        for name in self._element_names:
            setattr(result, name, fn(getattr(self, name)))
        return result

    def __add__(self, other: Vector) -> Vector:
        return self._elementwise(other, "+", lambda a, b: a + b)

    def __sub__(self, other: Vector) -> Vector:
        return self._elementwise(other, "-", lambda a, b: a - b)

    def __mul__(self, other: Any) -> Vector:
        if isinstance(other, Vector):
            return self._elementwise(other, "*", lambda a, b: a * b)
        return self._scaled(other, "*", lambda a, s: a * s)

    def __rmul__(self, other: Any) -> Vector:
        if isinstance(other, Vector):
            return NotImplemented
        return self._scaled(other, "*", lambda a, s: a * s)

    def __truediv__(self, scalar: float) -> Vector:
        return self._scaled(scalar, "/", lambda a, s: a / s)

    def __neg__(self) -> Vector:
        return self._transformed(lambda v: -v)

    def __pos__(self) -> Vector:
        return self._transformed(lambda v: +v)

    def __eq__(self, other: Any) -> Any:
        if type(self) is not type(other):
            return NotImplemented
        return all(
            getattr(self, name) == getattr(other, name)
            for name in self._element_names
        )
