#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

@interface User : NSObject
@property (nonatomic, strong) NSString *name;
@property (nonatomic, assign) NSInteger age;

- (instancetype)initWithName:(NSString *)name age:(NSInteger)age;
- (NSString *)greet;
@end

@implementation User

- (instancetype)initWithName:(NSString *)name age:(NSInteger)age {
    self = [super init];
    if (self) {
        _name = name;
        _age = age;
    }
    return self;
}

- (NSString *)greet {
    return [NSString stringWithFormat:@"Hello, %@", self.name];
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        User *user = [[User alloc] initWithName:@"Alice" age:30];
        NSLog(@"%@", [user greet]);
    }
    return 0;
}
