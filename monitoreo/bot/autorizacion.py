"""Resuelve qué puede hacer cada chat_id: vehículos visibles y permisos."""
from __future__ import annotations

from typing import Optional

from config import config
from monitoreo.models import AccesoTelegram, Vehiculo

MULTIPLE = "MULTIPLE"


class ServicioAccesos:
    """chat_id -> accesos activos; verificación de permisos por vehículo."""

    def es_admin(self, chat_id: str) -> bool:
        return chat_id in config.telegram.admin_chat_ids

    def accesos_de(self, chat_id: str):
        return (
            AccesoTelegram.objects.filter(chat_id=chat_id, activo=True)
            .select_related("vehiculo", "vehiculo__conductor")
        )

    def vehiculos_de(self, chat_id: str):
        if self.es_admin(chat_id):
            return Vehiculo.objects.select_related("conductor").all()
        return Vehiculo.objects.filter(
            accesos_telegram__chat_id=chat_id, accesos_telegram__activo=True,
        ).select_related("conductor").distinct()

    def acceso_para_vehiculo(self, chat_id: str, vehiculo: Vehiculo) -> Optional[AccesoTelegram]:
        return AccesoTelegram.objects.filter(chat_id=chat_id, vehiculo=vehiculo, activo=True).first()

    def puede_consultar(self, chat_id: str, vehiculo: Vehiculo) -> bool:
        return self.es_admin(chat_id) or self.acceso_para_vehiculo(chat_id, vehiculo) is not None

    def puede_controlar(self, chat_id: str, vehiculo: Vehiculo) -> bool:
        if self.es_admin(chat_id):
            return True
        acceso = self.acceso_para_vehiculo(chat_id, vehiculo)
        return bool(acceso and acceso.puede_controlar)

    def puede_invitar(self, chat_id: str, vehiculo: Vehiculo) -> bool:
        if self.es_admin(chat_id):
            return True
        acceso = self.acceso_para_vehiculo(chat_id, vehiculo)
        return bool(acceso and acceso.rol == "PROPIETARIO")

    def resolver_vehiculo(self, chat_id: str, placa: Optional[str]):
        """Devuelve ``(vehiculo, error)``. ``error`` es ``None`` si hay vehículo,
        ``MULTIPLE`` si hay varios y hace falta elegir, o un mensaje de error."""
        vehiculos = list(self.vehiculos_de(chat_id))

        if placa:
            placa_norm = placa.strip().upper().replace("-", "")
            for vehiculo in vehiculos:
                if vehiculo.placa.replace("-", "") == placa_norm:
                    return vehiculo, None
            return None, f"No tienes acceso a un vehículo con placa {placa.upper()}."

        if len(vehiculos) == 1:
            return vehiculos[0], None
        if not vehiculos:
            return None, "Todavía no tienes vehículos vinculados. Usa /vincular o /unirse."
        return None, MULTIPLE
