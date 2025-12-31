import java.util.*;
import java.io.*;

public class Main {
    private String name;
    private int value;
    
    public Main(String name, int value) {
        this.name = name;
        this.value = value;
    }
    
    public void sayHello() {
        System.out.println("Hello, " + name);
    }
    
    public static void main(String[] args) {
        Main app = new Main("Java", 42);
        app.sayHello();
    }
}

class User {
    private String username;
    
    public User(String username) {
        this.username = username;
    }
    
    public String getUsername() {
        return username;
    }
}
