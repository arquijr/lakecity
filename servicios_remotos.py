#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
servicios_remotos.py
Instala (si no estan) y configura SSH, FTP (vsftpd), RDP (xrdp) y VNC (TigerVNC)
en cualquier Linux con systemd (apt, dnf, yum, zypper, pacman) y crea los
usuarios automaticamente.

Uso (una sola instruccion):
    sudo python3 servicios_remotos.py

Solo pregunta: usuario base, contrasena y cuantos usuarios por servicio.
  - 1 usuario  -> se llama exactamente como el usuario base
  - N usuarios -> base1, base2, ... baseN
  - 0          -> ese servicio se omite
"""
import getpass
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# PERMISOS: edita estos valores antes de ejecutar si necesitas otros
# ----------------------------------------------------------------------
PERMISOS = {
    "sudo": False,            # True = los usuarios nuevos son administradores
    "shell": "/bin/bash",     # shell de los usuarios
    "modo_home": 0o750,       # permisos de la carpeta personal
    "ftp_escritura": True,    # FTP: pueden subir/borrar archivos
    "ftp_enjaular": True,     # FTP: cada usuario solo ve su carpeta personal
    "ssh_restringir": False,  # True = SSH solo para estos usuarios (+ quien ejecuta sudo)
    "vnc_resolucion": "1280x800",
}
FTP_PASV = (40000, 40100)
VNC_PUERTO_BASE = 5900        # usuario n -> pantalla :n -> puerto 5900+n

PAQUETES = {
    "ssh": {"apt": ["openssh-server"], "dnf": ["openssh-server"], "yum": ["openssh-server"],
            "zypper": ["openssh"], "pacman": ["openssh"]},
    "ftp": {"apt": ["vsftpd"], "dnf": ["vsftpd"], "yum": ["vsftpd"],
            "zypper": ["vsftpd"], "pacman": ["vsftpd"]},
    "rdp": {"apt": ["xrdp"], "dnf": ["xrdp"], "yum": ["xrdp"], "zypper": ["xrdp"], "pacman": None},
    "vnc": {"apt": ["tigervnc-standalone-server"], "dnf": ["tigervnc-server"],
            "yum": ["tigervnc-server"], "zypper": ["tigervnc"], "pacman": ["tigervnc"]},
}
BINARIOS = {
    "ssh": ["sshd"],
    "ftp": ["vsftpd"],
    "rdp": ["xrdp"],
    "vnc": ["vncserver", "tigervncserver", "Xtigervnc"],
}
SESIONES = ["icewm-session", "startxfce4", "mate-session", "startlxqt",
            "startplasma-x11", "cinnamon-session", "gnome-session"]
PATH_EXTRA = os.environ.get("PATH", "") + ":/usr/sbin:/sbin:/usr/local/sbin:/usr/local/bin"


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------
def info(msg):
    print(f"\033[1;34m[*]\033[0m {msg}")


def ok(msg):
    print(f"\033[1;32m[OK]\033[0m {msg}")


def aviso(msg):
    print(f"\033[1;33m[!]\033[0m {msg}")


def error(msg):
    print(f"\033[1;31m[X]\033[0m {msg}")


def run(cmd, entrada=None, check=True, capturar=False):
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", PATH=PATH_EXTRA)
    return subprocess.run(cmd, input=entrada, text=True, check=check, env=env,
                          stdout=subprocess.PIPE if capturar else None,
                          stderr=subprocess.STDOUT if capturar else None)


def buscar(binario):
    return shutil.which(binario, path=PATH_EXTRA)


def instalado(servicio):
    return any(buscar(b) for b in BINARIOS[servicio])


def detectar_gestor():
    for pm in ("apt-get", "dnf", "yum", "zypper", "pacman"):
        if buscar(pm):
            return "apt" if pm == "apt-get" else pm
    return None


def instalar_paquetes(pm, paquetes):
    cmds = {
        "apt": ["apt-get", "install", "-y"],
        "dnf": ["dnf", "install", "-y"],
        "yum": ["yum", "install", "-y"],
        "zypper": ["zypper", "--non-interactive", "install"],
        "pacman": ["pacman", "-S", "--needed", "--noconfirm"],
    }
    if pm == "apt":
        run(["apt-get", "update"], check=False)
    elif pm == "zypper":
        run(["zypper", "--non-interactive", "refresh"], check=False)
    r = run(cmds[pm] + paquetes, check=False)
    if r.returncode != 0 and pm in ("dnf", "yum"):
        aviso("Fallo la instalacion; intentando con el repositorio EPEL...")
        run(cmds[pm] + ["epel-release"], check=False)
        r = run(cmds[pm] + paquetes, check=False)
    return r.returncode == 0


def unidad_existe(nombre):
    r = run(["systemctl", "list-unit-files", f"{nombre}.service"], check=False, capturar=True)
    return nombre in (r.stdout or "")


def activar(servicio, reiniciar=True):
    run(["systemctl", "daemon-reload"], check=False)
    run(["systemctl", "enable", servicio], check=False)
    run(["systemctl", "restart" if reiniciar else "start", servicio], check=False)
    r = run(["systemctl", "is-active", servicio], check=False, capturar=True)
    return (r.stdout or "").strip() == "active"


def respaldo(ruta):
    ruta = Path(ruta)
    bak = Path(str(ruta) + ".bak")
    if ruta.exists() and not bak.exists():
        shutil.copy2(ruta, bak)


def escribir(ruta, contenido, modo=0o644, usuario=None):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido)
    os.chmod(ruta, modo)
    if usuario:
        p = pwd.getpwnam(usuario)
        os.chown(ruta, p.pw_uid, p.pw_gid)


def sesion_escritorio():
    for s in SESIONES:
        if buscar(s):
            return s
    return None


def abrir_puertos(puertos):
    """puertos: lista de ints o strings 'ini:fin'."""
    if buscar("ufw"):
        r = run(["ufw", "status"], check=False, capturar=True)
        if "inactive" not in (r.stdout or "").lower() and "active" in (r.stdout or "").lower():
            for p in puertos:
                run(["ufw", "allow", f"{str(p).replace(':', ':')}/tcp"], check=False, capturar=True)
            return
    if buscar("firewall-cmd"):
        if run(["firewall-cmd", "--state"], check=False, capturar=True).returncode == 0:
            for p in puertos:
                run(["firewall-cmd", "--permanent", f"--add-port={str(p).replace(':', '-')}/tcp"],
                    check=False, capturar=True)
            run(["firewall-cmd", "--reload"], check=False, capturar=True)


# ----------------------------------------------------------------------
# Usuarios
# ----------------------------------------------------------------------
def nombres(base, n):
    if n <= 0:
        return []
    if n == 1:
        return [base]
    return [f"{base}{i}" for i in range(1, n + 1)]


def grupo_admin():
    for g in ("sudo", "wheel"):
        try:
            import grp
            grp.getgrnam(g)
            return g
        except KeyError:
            continue
    return None


def crear_usuario(nombre, clave):
    existe = True
    try:
        pwd.getpwnam(nombre)
    except KeyError:
        existe = False
    if not existe:
        run(["useradd", "-m", "-s", PERMISOS["shell"], nombre])
    run(["chpasswd"], entrada=f"{nombre}:{clave}\n")
    home = pwd.getpwnam(nombre).pw_dir
    os.chmod(home, PERMISOS["modo_home"])
    if PERMISOS["sudo"]:
        g = grupo_admin()
        if g:
            run(["usermod", "-aG", g, nombre])
    return "actualizado" if existe else "creado"


# ----------------------------------------------------------------------
# Servicios
# ----------------------------------------------------------------------
def asegurar_instalado(servicio, pm):
    if instalado(servicio):
        ok(f"{servicio.upper()} ya esta instalado.")
        return True
    paquetes = PAQUETES[servicio].get(pm)
    if not paquetes:
        error(f"{servicio.upper()} no esta disponible en los repositorios oficiales de esta distribucion.")
        return False
    info(f"Instalando {servicio.upper()} ({' '.join(paquetes)})...")
    if instalar_paquetes(pm, paquetes) and instalado(servicio):
        ok(f"{servicio.upper()} instalado.")
        return True
    error(f"No se pudo instalar {servicio.upper()}.")
    return False


def config_ssh(users, admin):
    nombre = "ssh" if unidad_existe("ssh") else "sshd"
    if PERMISOS["ssh_restringir"]:
        cfg = Path("/etc/ssh/sshd_config")
        if admin and cfg.exists() and re.search(r"^\s*Include\s+/etc/ssh/sshd_config\.d", cfg.read_text(), re.M):
            drop = Path("/etc/ssh/sshd_config.d/00-acceso-usuarios.conf")
            lista = sorted(set(users + [admin]))
            escribir(drop, "AllowUsers " + " ".join(lista) + "\n")
            if run(["sshd", "-t"], check=False).returncode != 0:
                drop.unlink()
                aviso("Configuracion SSH invalida; se revirtio la restriccion de usuarios.")
        else:
            aviso("No se pudo restringir SSH de forma segura (sin sudo o sin carpeta sshd_config.d).")
    if activar(nombre):
        ok("SSH activo (puerto 22).")
    else:
        error("SSH no quedo activo; revisa: systemctl status " + nombre)
    abrir_puertos([22])


def config_ftp(users):
    conf = Path("/etc/vsftpd/vsftpd.conf")
    if not conf.exists():
        conf = Path("/etc/vsftpd.conf")
    respaldo(conf)
    Path("/usr/share/empty").mkdir(parents=True, exist_ok=True)
    lista_f = Path("/etc/vsftpd.user_list")
    previos = set(lista_f.read_text().split()) if lista_f.exists() else set()
    escribir(lista_f, "\n".join(sorted(previos | set(users))) + "\n")
    si = lambda v: "YES" if v else "NO"
    texto = f"""# Generado por servicios_remotos.py
listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable={si(PERMISOS['ftp_escritura'])}
local_umask=022
dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES
chroot_local_user={si(PERMISOS['ftp_enjaular'])}
allow_writeable_chroot=YES
secure_chroot_dir=/usr/share/empty
pam_service_name=vsftpd
userlist_enable=YES
userlist_deny=NO
userlist_file={lista_f}
pasv_enable=YES
pasv_min_port={FTP_PASV[0]}
pasv_max_port={FTP_PASV[1]}
"""
    escribir(conf, texto)
    if activar("vsftpd"):
        ok("FTP activo (puerto 21).")
    else:
        error("vsftpd no quedo activo; revisa: systemctl status vsftpd")
    abrir_puertos([21, f"{FTP_PASV[0]}:{FTP_PASV[1]}"])


def config_rdp(users):
    sesion = sesion_escritorio()
    if not sesion:
        aviso("No hay entorno de escritorio instalado (icewm, xfce, mate...). "
              "RDP necesita uno: instala, por ejemplo, 'icewm' o 'xfce4'.")
    else:
        for u in users:
            home = pwd.getpwnam(u).pw_dir
            escribir(Path(home) / ".xsession", f"#!/bin/sh\nexec {sesion}\n", 0o755, u)
    try:
        import grp
        grp.getgrnam("ssl-cert")
        run(["usermod", "-aG", "ssl-cert", "xrdp"], check=False)
    except KeyError:
        pass
    if activar("xrdp"):
        ok("RDP activo (puerto 3389).")
    else:
        error("xrdp no quedo activo; revisa: systemctl status xrdp")
    abrir_puertos([3389])


def config_vnc(users, clave):
    sesion = sesion_escritorio()
    vnc = buscar("vncserver") or buscar("tigervncserver")
    passwd_bin = buscar("vncpasswd") or buscar("tigervncpasswd")
    if not (vnc and passwd_bin):
        error("No se encontraron vncserver/vncpasswd.")
        return
    if not sesion:
        aviso("No hay entorno de escritorio instalado; VNC mostrara pantalla gris.")
    if len(clave) > 8:
        aviso("VNC solo usa los primeros 8 caracteres de la contrasena.")
    cifrada = subprocess.run([passwd_bin, "-f"], input=clave + "\n", capture_output=True).stdout
    puertos = []
    for n, u in enumerate(users, start=1):
        home = Path(pwd.getpwnam(u).pw_dir)
        p = pwd.getpwnam(u)
        (home / ".vnc").mkdir(exist_ok=True)
        os.chown(home / ".vnc", p.pw_uid, p.pw_gid)
        (home / ".vnc" / "passwd").write_bytes(cifrada)
        os.chmod(home / ".vnc" / "passwd", 0o600)
        os.chown(home / ".vnc" / "passwd", p.pw_uid, p.pw_gid)
        escribir(home / ".vnc" / "xstartup",
                 "#!/bin/sh\nunset SESSION_MANAGER\nunset DBUS_SESSION_BUS_ADDRESS\n"
                 + (f"exec {sesion}\n" if sesion else "xsetroot -solid grey\n"), 0o755, u)
        unidad = f"vnc-{u}"
        escribir(f"/etc/systemd/system/{unidad}.service", f"""[Unit]
Description=VNC (TigerVNC) de {u} en :{n}
After=network.target

[Service]
Type=simple
User={u}
WorkingDirectory={home}
ExecStartPre=-{vnc} -kill :{n}
ExecStart={vnc} :{n} -fg -geometry {PERMISOS['vnc_resolucion']} -depth 24 -localhost no
ExecStop=-{vnc} -kill :{n}
Restart=on-failure

[Install]
WantedBy=multi-user.target
""")
        activar(unidad)
        puertos.append(VNC_PUERTO_BASE + n)
        ok(f"VNC: {u} -> pantalla :{n} (puerto {VNC_PUERTO_BASE + n})")
    abrir_puertos(puertos)


# ----------------------------------------------------------------------
# Entrada de datos
# ----------------------------------------------------------------------
def pedir_usuario():
    while True:
        u = input("Usuario base (ej. alumno): ").strip()
        if re.fullmatch(r"[a-z_][a-z0-9_-]{0,25}", u):
            return u
        error("Usa solo minusculas, numeros, guion o guion bajo (max. 26).")


def pedir_clave():
    while True:
        c1 = getpass.getpass("Contrasena: ")
        if not c1:
            error("La contrasena no puede estar vacia.")
            continue
        if c1 == getpass.getpass("Repite la contrasena: "):
            return c1
        error("No coinciden.")


def pedir_cantidad(servicio):
    while True:
        v = input(f"Cuantos usuarios para {servicio} (0 = omitir): ").strip()
        if v.isdigit() and int(v) <= 200:
            return int(v)
        error("Escribe un numero entre 0 y 200.")


def ip_local():
    r = run(["hostname", "-I"], check=False, capturar=True)
    return (r.stdout or "").split()[0] if r.stdout and r.stdout.split() else "IP_DEL_EQUIPO"


def main():
    if os.geteuid() != 0:
        if buscar("sudo"):
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        error("Ejecuta este script como root.")
        sys.exit(1)
    if not buscar("systemctl"):
        error("Se requiere una distribucion con systemd.")
        sys.exit(1)
    pm = detectar_gestor()
    if not pm:
        error("Gestor de paquetes no reconocido (apt, dnf, yum, zypper, pacman).")
        sys.exit(1)

    admin = os.environ.get("SUDO_USER")
    print("=== Instalador de SSH / FTP / RDP / VNC ===\n")
    base = pedir_usuario()
    clave = pedir_clave()
    cant = {s: pedir_cantidad(s.upper()) for s in ("ssh", "ftp", "rdp", "vnc")}
    if not any(cant.values()):
        aviso("No se eligio ningun servicio. Nada que hacer.")
        return

    lista = {s: nombres(base, n) for s, n in cant.items()}
    todos = sorted({u for us in lista.values() for u in us})
    print()
    for u in todos:
        info(f"Usuario {u}: {crear_usuario(u, clave)}")

    resumen = []
    ip = ip_local()
    for s in ("ssh", "ftp", "rdp", "vnc"):
        if not lista[s]:
            continue
        print()
        info(f"--- {s.upper()} ---")
        if not asegurar_instalado(s, pm):
            continue
        if s == "ssh":
            config_ssh(lista[s], admin)
            resumen.append(f"SSH : ssh {lista[s][0]}@{ip}")
        elif s == "ftp":
            config_ftp(lista[s])
            resumen.append(f"FTP : ftp://{ip}  (usuarios: {', '.join(lista[s])})")
        elif s == "rdp":
            config_rdp(lista[s])
            resumen.append(f"RDP : {ip}:3389  (usuarios: {', '.join(lista[s])})")
        elif s == "vnc":
            config_vnc(lista[s], clave)
            for n, u in enumerate(lista[s], start=1):
                resumen.append(f"VNC : {ip}:{VNC_PUERTO_BASE + n}  ({u})")

    print("\n=== RESUMEN ===")
    for linea in resumen:
        print("  " + linea)
    print("\nListo.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(130)
