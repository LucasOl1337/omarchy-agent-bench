# Accessibility inside a bench

The bench has a private session D-Bus. On the tested Arch installation,
at-spi2-core 2.60.6 selected dbus-broker for its separate accessibility bus.
That broker tried to activate the accessibility registry through
org.freedesktop.systemd1 on the private session bus, where no user manager
exists. The bus address was available, but registry activation failed and CUA
reported NameHasNoOwner. An available socket alone was not a healthy AX route.

Boot now sets `ATSPI_DBUS_IMPLEMENTATION=dbus-daemon` before dbus-run-session.
The accessibility bus can activate its registry locally inside the bench's
cgroup and environment. The setting is scoped to newly started benches; it
does not modify the user's D-Bus, desktop preferences, or existing benches.
Owners may restart their own bench after preserving work. Do not restart a
neighbor just to apply this change.

The upstream launcher supports this selection explicitly:
[bus launcher source](https://github.com/GNOME/at-spi2-core/blob/main/bus/at-spi-bus-launcher.c).
The session bus and accessibility bus are separate protocols:
[upstream overview](https://github.com/GNOME/at-spi2-core/blob/main/bus/README.md).

Validation should check registry activation and a real application's tree.
Registry availability alone does not prove that a particular toolkit exposes
all controls, that coordinates are correct, or that an input had its intended
effect. Use screenshots and read back the result of native actions.
