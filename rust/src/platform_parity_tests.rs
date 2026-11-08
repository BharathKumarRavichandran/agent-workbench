//! F3: real required primitives, through the production rustix backend.
use crate::bundles;
use rustix::fs::{self as rfs, AtFlags, FlockOperation, Mode, OFlags};
use std::{env, fs, io::Write, os::unix::fs::symlink, path::PathBuf};

#[test]
fn required_filesystem_primitives() {
    let scratch = env::var_os("TMPDIR").expect("explicit scratch TMPDIR required");
    let temporary = tempfile::tempdir_in(scratch).unwrap();
    let base = env::var_os("AW_PLATFORM_BASE")
        .map(PathBuf::from)
        .unwrap_or_else(|| temporary.path().to_path_buf());
    let root = base.join("fixture");
    fs::create_dir(&root).unwrap();
    let parent = bundles::open_directory(&root).unwrap();
    let file = rfs::openat(
        &parent,
        "private",
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL,
        Mode::from_raw_mode(0o600),
    )
    .unwrap();
    rfs::fchmod(&file, Mode::from_raw_mode(0o600)).unwrap();
    assert_eq!(rfs::fstat(&file).unwrap().st_mode & 0o7777, 0o600);
    let mut file = fs::File::from(file);
    file.write_all(b"owned").unwrap();
    file.sync_all().unwrap();
    drop(file);
    rfs::fsync(&parent).unwrap();
    symlink(".", root.join("alias")).unwrap();
    let error = rfs::openat(
        &parent,
        "alias",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW,
        Mode::empty(),
    )
    .unwrap_err();
    assert!(matches!(
        error,
        rustix::io::Errno::LOOP | rustix::io::Errno::NOTDIR
    ));
    assert_eq!(
        fs::read_link(root.join("alias")).unwrap(),
        PathBuf::from(".")
    );
    rfs::linkat(&parent, "private", &parent, "published", AtFlags::empty()).unwrap();
    assert_eq!(
        rfs::statat(&parent, "private", AtFlags::empty())
            .unwrap()
            .st_ino,
        rfs::statat(&parent, "published", AtFlags::empty())
            .unwrap()
            .st_ino
    );
    assert_eq!(
        rfs::linkat(&parent, "private", &parent, "published", AtFlags::empty()).unwrap_err(),
        rustix::io::Errno::EXIST
    );
    for name in ["stage", "occupied"] {
        rfs::mkdirat(&parent, name, Mode::from_raw_mode(0o700)).unwrap();
        let directory = bundles::open_directory(&root.join(name)).unwrap();
        rfs::fchmod(&directory, Mode::from_raw_mode(0o700)).unwrap();
        assert_eq!(rfs::fstat(&directory).unwrap().st_mode & 0o7777, 0o700);
    }
    assert_eq!(
        bundles::rename_new("stage", &parent, "occupied", &parent)
            .unwrap_err()
            .kind(),
        std::io::ErrorKind::AlreadyExists
    );
    bundles::rename_new("stage", &parent, "bundle", &parent).unwrap();
    assert!(root.join("occupied").is_dir() && root.join("bundle").is_dir());
    let lock = rfs::openat(
        &parent,
        "lock",
        OFlags::RDWR | OFlags::CREATE,
        Mode::from_raw_mode(0o600),
    )
    .unwrap();
    rfs::fchmod(&lock, Mode::from_raw_mode(0o600)).unwrap();
    assert_eq!(rfs::fstat(&lock).unwrap().st_mode & 0o7777, 0o600);
    let other = rfs::openat(&parent, "lock", OFlags::RDWR, Mode::empty()).unwrap();
    rfs::flock(&lock, FlockOperation::NonBlockingLockExclusive).unwrap();
    assert_eq!(
        rfs::flock(&other, FlockOperation::NonBlockingLockExclusive).unwrap_err(),
        rustix::io::Errno::WOULDBLOCK
    );
    drop(other);
    drop(lock);
    let pinned = rfs::fstat(&parent).unwrap();
    let detached = base.join("detached");
    fs::rename(&root, &detached).unwrap();
    fs::create_dir(&root).unwrap();
    fs::write(root.join("foreign"), b"outside").unwrap();
    assert_eq!(rfs::fstat(&parent).unwrap().st_ino, pinned.st_ino);
    let replacement = bundles::open_directory(&root).unwrap();
    assert_ne!(rfs::fstat(&replacement).unwrap().st_ino, pinned.st_ino);
    rfs::unlinkat(&parent, "published", AtFlags::empty()).unwrap();
    assert_eq!(fs::read(root.join("foreign")).unwrap(), b"outside");
    assert!(!detached.join("published").exists());
    fs::remove_file(root.join("foreign")).unwrap();
    fs::remove_dir(&root).unwrap();
    fs::rename(&detached, &root).unwrap();
    rfs::fsync(&parent).unwrap();
}
